"""Report stream -> project state projection and fever chart.

Chosen execution rules (see docs/DECISIONS.md):

* Reports are an append-only log.  The state is a *pure fold* over the log
  sorted by ``(report time, report id)``; the id tiebreak makes the order
  total and deterministic.  A report whose timestamp is earlier than the
  latest known one is therefore **inserted back at its correct position**
  and everything is recomputed -- it is never rejected for being late.
* The critical chain identified at planning time is **frozen** during
  execution.  Updates only move buffer consumption; the chain does not
  jump around.  Changing scope means replanning (a new plan).
* Per-task status is the effect of the *last* report for that task in that
  total order (``done``, or ``remaining`` with the reported remaining
  duration).  History is preserved verbatim; only the projection changes.

Buffer penetration (project buffer): for each critical-chain task compare
its finish against the plan -- actual finish for completed tasks,
``now + remaining`` for the open task; future tasks do not contribute.
The maximum overrun in workdays is the projected push-out of the chain
end; penetration % = push-out / PB size.  The same computation is exposed
for every feeding buffer.
"""
from __future__ import annotations

from .worktime import parse_dt, resolve_timezone, work_offset


def project_tz(plan: dict, tasks_payload: dict | None = None):
    """Effective timezone of a stored project.

    New plans carry ``timezone`` explicitly; plans written by older
    releases do not -- fall back to the payload field (filled by the
    one-time migration), then to UTC.
    """
    spec = plan.get("timezone")
    if spec is None and isinstance(tasks_payload, dict):
        spec = tasks_payload.get("timezone")
    return resolve_timezone(spec or "UTC")


def _task_fold(tasks, report_seq):
    remaining = {tid: tasks[tid]["agg"] for tid in tasks}
    status = {tid: "not_started" for tid in tasks}
    actual_finish_day: dict[str, float | None] = {tid: None for tid in tasks}
    last_report_at: dict[str, str | None] = {tid: None for tid in tasks}
    last_report_id: dict[str, str | None] = {tid: None for tid in tasks}

    for rep in report_seq:
        tid = rep["task_id"]
        at_day = rep["_at_day"]
        if rep["kind"] == "done" or (
                rep["kind"] == "remaining" and rep["remaining_days"] == 0):
            status[tid] = "done"
            remaining[tid] = 0.0
            actual_finish_day[tid] = at_day
        else:
            status[tid] = "in_progress"
            remaining[tid] = rep["remaining_days"]
        last_report_at[tid] = rep["at"]
        last_report_id[tid] = rep["report_id"]

    return remaining, status, actual_finish_day, last_report_at, last_report_id


def sorted_reports(reports, project_start, tz="UTC"):
    """Return reports in the total order (report time, report id).

    Arrival order is irrelevant: this ordering is what makes out-of-order
    submissions fold to the identical state as in-order ones.
    """
    seq = []
    for rep in reports:
        at = parse_dt(rep["at"], tz)
        seq.append({**rep, "at": at.isoformat(), "_dt": at,
                    "_at_day": work_offset(project_start, at, tz)})
    seq.sort(key=lambda r: (r["_dt"], r["report_id"]))
    return seq


def _chain_pushout(chain_ids, tasks, schedule, remaining, status,
                   actual_finish_day, now_day):
    """Max overrun of chain tasks vs their planned finish (>= 0)."""
    push = 0.0
    per_task = {}
    for tid in chain_ids:
        planned_finish = schedule[tid]["finish_day"]
        if status[tid] == "done":
            projected = actual_finish_day[tid]
        elif status[tid] == "in_progress":
            projected = now_day + remaining[tid]
        else:
            projected = None
        overrun = 0.0 if projected is None else max(0.0,
                                                     projected - planned_finish)
        per_task[tid] = {"planned_finish_day": planned_finish,
                         "projected_finish_day": projected,
                         "overrun_days": overrun}
        push = max(push, overrun)
    return push, per_task


def _pct(push: float, size: float) -> float:
    if size <= 1e-12:
        return 0.0 if push <= 1e-9 else 100.0
    return round(push / size * 100.0, 6)


def project_state(plan: dict, tasks: dict, reports: list[dict],
                  at: str | None = None, tz=None,
                  tasks_payload: dict | None = None) -> dict:
    """Project the full state, optionally replaying only up to ``at``.

    ``at=None`` means "the latest known report moment"; if there are no
    reports, the project start is used.  Replay by *moment* includes every
    report whose timestamp <= ``at`` (two managers reporting at the same
    instant are both visible at that instant).

    ``tz`` is the project timezone (IANA name / fixed offset).  The
    working calendar and every naive timestamp are interpreted in it.
    """
    zone = project_tz(plan, tasks_payload) if tz is None else resolve_timezone(tz)
    project_start = parse_dt(plan["project_start"], zone)
    seq = sorted_reports(reports, project_start, zone)
    if at is not None:
        cutoff = parse_dt(at, zone)
        seq = [r for r in seq if r["_dt"] <= cutoff]
        now = cutoff
    elif seq:
        now = seq[-1]["_dt"]
    else:
        now = project_start
    now_day = work_offset(project_start, now, zone)

    (remaining, status, actual_finish_day,
     last_report_at, last_report_id) = _task_fold(tasks, seq)

    cc_ids = plan["critical_chain"]
    cc_total = sum(tasks[t]["agg"] for t in cc_ids)
    earned_cc = sum(
        tasks[t]["agg"] if status[t] == "done"
        else max(0.0, tasks[t]["agg"] - remaining[t]) if status[t] == "in_progress"
        else 0.0 for t in cc_ids)
    completion_pct = round(earned_cc / cc_total * 100.0, 6) if cc_total > 1e-12 \
        else 100.0

    pb_push, pb_tasks = _chain_pushout(
        cc_ids, tasks, plan["schedule"], remaining, status,
        actual_finish_day, now_day)
    pb_pct = _pct(pb_push, plan["project_buffer"]["size_days"])

    feeding = []
    for fb in plan["feeding_buffers"]:
        f_push, f_tasks = _chain_pushout(
            fb["chain"], tasks, plan["schedule"], remaining, status,
            actual_finish_day, now_day)
        feeding.append({
            "id": fb["id"],
            "feeds_task": fb["feeds_task"],
            "size_days": fb["size_days"],
            "consumed_days": round(f_push, 6),
            "consumed_pct": _pct(f_push, fb["size_days"]),
        })

    # rows in plan task order, deterministic
    task_order = sorted(
        tasks, key=lambda t: (plan["schedule"][t]["start_day"], t))
    task_rows = []
    for tid in task_order:
        task_rows.append({
            "task_id": tid,
            "name": tasks[tid]["name"],
            "status": status[tid],
            "remaining_days": round(remaining[tid], 6),
            "aggressive_days": tasks[tid]["agg"],
            "on_critical_chain": tid in set(cc_ids),
            "actual_finish_day": actual_finish_day[tid],
            "last_report_at": last_report_at[tid],
            "last_report_id": last_report_id[tid],
        })

    yellow = plan["thresholds"]["yellow_pct"]
    red = plan["thresholds"]["red_pct"]
    zone = "green" if pb_pct < yellow else "yellow" if pb_pct < red else "red"

    return {
        "at": now.isoformat(),
        "now_day": round(now_day, 6),
        "tasks": task_rows,
        "critical_chain_completion_pct": completion_pct,
        "project_buffer": {
            "size_days": plan["project_buffer"]["size_days"],
            "consumed_days": round(pb_push, 6),
            "consumed_pct": pb_pct,
        },
        "feeding_buffers": feeding,
        "zone": zone,
        "report_count": len(seq),
    }
