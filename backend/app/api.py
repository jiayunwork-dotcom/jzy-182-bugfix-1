"""HTTP interface layer.

All computation lives in the other modules; this file only wires HTTP to
them.  Bodies are parsed as raw JSON and handed to ``validation`` so every
rejection names an exact field and is returned as HTTP 422 with a uniform
shape ``{errors: [{field, message}]}``.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Request

from . import projection
from . import fever
from .errors import ApiError
from .scheduler import build_plan
from .validation import ValidationIssue, validate_project, validate_report

router = APIRouter(prefix="/api")


def _store(req: Request):
    return req.app.state.store


def _json(request: Request) -> dict:
    try:
        return request.state.body
    except AttributeError:
        raise ApiError([{"field": "body", "message": "JSON body is required"}])


def _load(req: Request, project_id: str) -> dict:
    project = _store(req).get_project(project_id)
    if project is None:
        raise ApiError([{"field": "project_id",
                         "message": "project not found"}], status=404)
    return project


@router.get("/health")
def health():
    return {"ok": True}


@router.post("/projects", status_code=201)
def create_project(request: Request):
    body = _json(request)
    try:
        validated = validate_project(body)
        plan = build_plan(validated)
    except ValidationIssue as exc:
        raise ApiError([exc.to_dict()])

    pid = uuid4().hex
    store = _store(request)
    normalized = {
        "name": validated["name"],
        "start_time": validated["start_time"],
        "timezone": validated["timezone"],
        "tasks": validated["tasks"],
        "task_order": validated["task_order"],
        "resources": validated["resources"],
        "thresholds": validated["thresholds"],
    }
    try:
        store.create_project(
            pid, validated["name"], normalized, plan,
            datetime.now(timezone.utc).isoformat())
    except Exception as exc:  # pragma: no cover - defensive
        raise ApiError([{"field": "id", "message": str(exc)}], status=409)
    return {"id": pid, "name": validated["name"], "plan": plan}


@router.get("/projects")
def list_projects(request: Request):
    return {"projects": _store(request).list_projects()}


@router.get("/projects/{project_id}")
def get_project(request: Request, project_id: str):
    return _load(request, project_id)


@router.get("/projects/{project_id}/plan")
def get_plan(request: Request, project_id: str):
    project = _load(request, project_id)
    return {"payload": project["payload"], "plan": project["plan"]}


@router.get("/projects/{project_id}/state")
def get_state(request: Request, project_id: str, at: str | None = None):
    project = _load(request, project_id)
    reports = _store(request).list_reports(project_id)
    tz = project["payload"].get("timezone", "UTC")
    try:
        state = projection.project_state(
            project["plan"], project["payload"]["tasks"], reports, at=at,
            tz=tz, tasks_payload=project["payload"])
    except ValueError as exc:  # bad 'at' timestamp
        raise ApiError([{"field": "at", "message": str(exc)}])
    return state


@router.get("/projects/{project_id}/fever")
def get_fever(request: Request, project_id: str):
    project = _load(request, project_id)
    reports = _store(request).list_reports(project_id)
    tz = project["payload"].get("timezone", "UTC")
    points = fever.trajectory(
        project["plan"], project["payload"]["tasks"], reports,
        tz=tz, tasks_payload=project["payload"])
    return {"thresholds": project["plan"]["thresholds"],
            "timezone": project["plan"].get("timezone", tz),
            "points": points}


@router.post("/projects/{project_id}/reports")
def post_report(request: Request, project_id: str):
    project = _load(request, project_id)
    body = _json(request)
    tz = project["payload"].get("timezone", "UTC")
    try:
        report = validate_report(
            body, set(project["payload"]["tasks"].keys()), tz)
    except ValidationIssue as exc:
        raise ApiError([exc.to_dict()])

    inserted = _store(request).add_report(
        project_id, report, datetime.now(timezone.utc).isoformat())
    reports = _store(request).list_reports(project_id)
    state = projection.project_state(
        project["plan"], project["payload"]["tasks"], reports,
        tz=tz, tasks_payload=project["payload"])
    return {"inserted": inserted,
            "duplicate": not inserted,
            "report_id": report["report_id"],
            "state": state}
