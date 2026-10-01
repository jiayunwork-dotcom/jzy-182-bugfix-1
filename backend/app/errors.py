"""Uniform API error type.

Every non-2xx response has the shape ``{"errors": [{"field", "message"}]}``.
"""
from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse


class ApiError(Exception):
    def __init__(self, errors: list[dict[str, str]], status: int = 422):
        super().__init__(str(errors))
        self.errors = errors
        self.status = status


async def api_error_handler(request: Request, exc: ApiError):
    return JSONResponse(status_code=exc.status,
                        content={"errors": exc.errors})
