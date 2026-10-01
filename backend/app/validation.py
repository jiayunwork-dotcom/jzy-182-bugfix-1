"""Network modeling & input validation.

Every rejection is raised as :class:`ValidationIssue` carrying an exact
``field`` path, e.g. ``tasks[3].demands.engineer`` or
``thresholds.yellow_pct``.  The API layer maps these to HTTP 422.

Validated inputs are returned as plain dicts so the other core modules do
not depend on pydantic.
"""
from __future__ import annotations

from typing import Any


class ValidationIssue(Exception):
    def __init__(self, field: str, message: str):
        super().__init__(f"{field}: {message}")
        self.field = field
        self.message = message

    def to_dict(self) -> dict[str, str]:
        return {"field": self.field, "message": self.message}


def validate_project(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate a raw project payload (already parsed from JSON).

    Returns a normalized dict:
        {name, start_time|None,
         resources: {id: {id,name,capacity}},
         tasks:     {id: {id,name,preds,demands,agg,cons}}}
    Raises ValidationIssue on the first concrete problem found.
    """
    if not isinstance(payload, dict):
        raise ValidationIssue("body", "payload must be an object")

    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValidationIssue("name", "project name is required")

    # ----- thresholds -------------------------------------------------- #
    th = payload.get("thresholds")
    if not isinstance(th, dict):
        raise ValidationIssue("thresholds", "thresholds object is required")
    for key in ("yellow_pct", "red_pct"):
        v = th.get(key)
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            raise ValidationIssue(f"thresholds.{key}", "must be a number")
        if not 0 <= v <= 100:
            raise ValidationIssue(f"thresholds.{key}",
                                  "must be between 0 and 100")
    if th["yellow_pct"] >= th["red_pct"]:
        raise ValidationIssue("thresholds.yellow_pct",
                              "yellow threshold must be strictly below red")

    # ----- resources --------------------------------------------------- #
    raw_resources = payload.get("resources", [])
    if not isinstance(raw_resources, list):
        raise ValidationIssue("resources", "must be a list")
    resources: dict[str, dict] = {}
    for i, r in enumerate(raw_resources):
        base = f"resources[{i}]"
        if not isinstance(r, dict):
            raise ValidationIssue(base, "must be an object")
        rid = r.get("id")
        if not isinstance(rid, str) or not rid.strip():
            raise ValidationIssue(f"{base}.id", "resource id is required")
        if rid in resources:
            raise ValidationIssue(f"{base}.id", f"duplicate resource id {rid!r}")
        if not isinstance(r.get("name"), str) or not r["name"].strip():
            raise ValidationIssue(f"{base}.name", "resource name is required")
        cap = r.get("capacity")
        if not isinstance(cap, (int, float)) or isinstance(cap, bool):
            raise ValidationIssue(f"{base}.capacity", "must be a number")
        if cap < 0:
            raise ValidationIssue(f"{base}.capacity", "must not be negative")
        resources[rid] = {"id": rid, "name": r["name"], "capacity": float(cap)}

    # ----- tasks ------------------------------------------------------- #
    raw_tasks = payload.get("tasks", [])
    if not isinstance(raw_tasks, list):
        raise ValidationIssue("tasks", "must be a list")
    if len(raw_tasks) == 0:
        raise ValidationIssue("tasks", "at least one task is required")

    tasks: dict[str, dict] = {}
    order: list[str] = []
    for i, t in enumerate(raw_tasks):
        base = f"tasks[{i}]"
        if not isinstance(t, dict):
            raise ValidationIssue(base, "must be an object")
        tid = t.get("id")
        if not isinstance(tid, str) or not tid.strip():
            raise ValidationIssue(f"{base}.id", "task id is required")
        if tid in tasks:
            raise ValidationIssue(f"{base}.id", f"duplicate task id {tid!r}")
        if not isinstance(t.get("name"), str) or not t["name"].strip():
            raise ValidationIssue(f"{base}.name", "task name is required")

        agg = t.get("aggressive_days")
        cons = t.get("conservative_days")
        if not isinstance(agg, (int, float)) or isinstance(agg, bool):
            raise ValidationIssue(f"{base}.aggressive_days", "must be a number")
        if not isinstance(cons, (int, float)) or isinstance(cons, bool):
            raise ValidationIssue(f"{base}.conservative_days", "must be a number")
        if agg < 0:
            raise ValidationIssue(f"{base}.aggressive_days",
                                  "duration must not be negative")
        if cons < 0:
            raise ValidationIssue(f"{base}.conservative_days",
                                  "duration must not be negative")
        if agg > cons:
            raise ValidationIssue(f"{base}.aggressive_days",
                                  "aggressive duration must not exceed "
                                  "conservative duration")

        preds = t.get("predecessors", [])
        if not isinstance(preds, list) or not all(
                isinstance(p, str) for p in preds):
            raise ValidationIssue(f"{base}.predecessors",
                                  "must be a list of task id strings")

        demands_in = t.get("demands", {})
        if not isinstance(demands_in, dict):
            raise ValidationIssue(f"{base}.demands",
                                  "must be an object of resourceId -> amount")
        demands: dict[str, float] = {}
        for rid, amount in demands_in.items():
            if rid not in resources:
                raise ValidationIssue(
                    f"{base}.demands.{rid}",
                    f"references unknown resource {rid!r}")
            if not isinstance(amount, (int, float)) or isinstance(amount, bool):
                raise ValidationIssue(f"{base}.demands.{rid}",
                                      "required amount must be a number")
            if amount < 0:
                raise ValidationIssue(f"{base}.demands.{rid}",
                                      "required amount must not be negative")
            if amount > resources[rid]["capacity"]:
                raise ValidationIssue(
                    f"{base}.demands.{rid}",
                    f"task requires {amount:g} of resource {rid!r} but its "
                    f"total capacity is {resources[rid]['capacity']:g}")
            if amount != 0:
                demands[rid] = float(amount)

        tasks[tid] = {
            "id": tid,
            "name": t["name"],
            "preds": list(preds),
            "demands": demands,
            "agg": float(agg),
            "cons": float(cons),
        }
        order.append(tid)

    # ----- predecessor references & cycle ------------------------------ #
    for tid in order:
        t = tasks[tid]
        base = f"tasks[{order.index(tid)}].predecessors"
        for p in t["preds"]:
            if p not in tasks:
                raise ValidationIssue(base,
                                      f"references unknown task {p!r}")
            if p == tid:
                raise ValidationIssue(base, "task cannot depend on itself")
        if len(set(t["preds"])) != len(t["preds"]):
            raise ValidationIssue(base, "duplicate predecessor entry")

    _assert_acyclic(tasks, order)

    start = payload.get("start_time")
    if start is not None and not isinstance(start, str):
        raise ValidationIssue("start_time", "must be an ISO datetime string")

    return {
        "name": name.strip(),
        "start_time": start,
        "tasks": tasks,
        "task_order": order,
        "resources": resources,
        "thresholds": {"yellow_pct": float(th["yellow_pct"]),
                       "red_pct": float(th["red_pct"])},
    }


def _assert_acyclic(tasks: dict[str, dict], order: list[str]) -> None:
    """Kahn's algorithm; name a node still on the queue at failure time."""
    indeg = {tid: len(tasks[tid]["preds"]) for tid in order}
    successors: dict[str, list[str]] = {tid: [] for tid in order}
    for tid in order:
        for p in tasks[tid]["preds"]:
            successors[p].append(tid)
    ready = [tid for tid in order if indeg[tid] == 0]
    seen = 0
    while ready:
        cur = ready.pop()
        seen += 1
        for s in successors[cur]:
            indeg[s] -= 1
            if indeg[s] == 0:
                ready.append(s)
    if seen != len(order):
        stuck = next(tid for tid in order if indeg[tid] > 0)
        idx = order.index(stuck)
        raise ValidationIssue(
            f"tasks[{idx}].predecessors",
            f"dependency cycle detected (task {stuck!r} is part of it)")


# ------------------------------ report ----------------------------------- #

def validate_report(payload: dict[str, Any], task_ids: set[str]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValidationIssue("body", "payload must be an object")
    rid = payload.get("report_id")
    if not isinstance(rid, str) or not rid.strip():
        raise ValidationIssue("report_id", "report id is required")
    tid = payload.get("task_id")
    if not isinstance(tid, str) or not tid.strip():
        raise ValidationIssue("task_id", "task id is required")
    if tid not in task_ids:
        raise ValidationIssue("task_id", f"unknown task {tid!r}")
    at = payload.get("at")
    if not isinstance(at, str) or not at.strip():
        raise ValidationIssue("at", "ISO datetime is required")
    kind = payload.get("kind")
    if kind not in ("remaining", "done"):
        raise ValidationIssue("kind", "must be 'remaining' or 'done'")
    remaining = payload.get("remaining_days")
    if kind == "remaining":
        if not isinstance(remaining, (int, float)) or isinstance(remaining, bool):
            raise ValidationIssue("remaining_days",
                                  "number required for kind='remaining'")
        if remaining < 0:
            raise ValidationIssue("remaining_days",
                                  "remaining duration must not be negative")
    elif remaining is not None:
        raise ValidationIssue("remaining_days",
                              "must be omitted when kind='done'")
    return {"report_id": rid, "task_id": tid, "at": at, "kind": kind,
            "remaining_days": float(remaining) if remaining is not None else None}
