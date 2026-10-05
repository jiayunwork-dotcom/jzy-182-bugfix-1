"""FastAPI application: serves the built React UI and the JSON API."""
from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import router as api_router
from .errors import ApiError, api_error_handler
from .store import Store

STATIC_DIR = Path(os.environ.get("CCPM_STATIC_DIR", "/app/static"))

app = FastAPI(title="CCPM progress platform")
app.state.store = Store(os.environ.get("CCPM_DB_PATH", "/data/ccpm.db"))
# One-time, idempotent backfill: legacy projects get their working timezone
# inferred from stored offsets and their stored plan rebuilt in that zone.
# Pure-Z data resolves to UTC and rebuilds byte-identically (see
# docs/DECISIONS.md, section 6).
app.state.store.migrate_timezones()


@app.middleware("http")
async def parse_raw_body(request: Request, call_next):
    """Capture raw JSON so the validation layer owns all field errors."""
    ctype = request.headers.get("content-type", "")
    if request.method in ("POST", "PUT", "PATCH") and "json" in ctype:
        try:
            request.state.body = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            return JSONResponse(
                status_code=422,
                content={"errors": [{"field": "body",
                                     "message": "body is not valid JSON"}]})
    return await call_next(request)


@app.exception_handler(RequestValidationError)
async def pydantic_error_handler(request: Request, exc: RequestValidationError):
    errors = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"] if p != "body")
        errors.append({"field": loc or "body", "message": err["msg"]})
    return JSONResponse(status_code=422, content={"errors": errors})


app.include_router(api_router)
app.add_exception_handler(ApiError, api_error_handler)

if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"),
              name="assets")

    @app.get("/")
    async def index():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
        # Never shadow the API; everything else falls back to the SPA shell.
        if full_path.startswith("api/"):
            return JSONResponse(status_code=404,
                                content={"detail": "not found"})
        candidate = STATIC_DIR / full_path
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(STATIC_DIR / "index.html")
