"""Fever-chart queries.

A fever point is the projected state *at a report instant*; querying the
state at that instant reproduces every field of the point, so history is a
pure replay (prefix fold) -- no snapshots are stored.
"""
from __future__ import annotations

from . import projection
from .worktime import parse_dt, resolve_timezone


def trajectory(plan: dict, tasks: dict, reports: list[dict],
               tz=None, tasks_payload: dict | None = None) -> list[dict]:
    """One point per report in the total order (at, report_id).

    A point equals the state reached with every report whose timestamp is
    <= the point timestamp, so two reports sharing one instant describe the
    same unique state at that instant; the report_id tiebreak only fixes
    their listing order.
    """
    zone = projection.project_tz(plan, tasks_payload) if tz is None \
        else resolve_timezone(tz)
    project_start = parse_dt(plan["project_start"], zone)
    seq = projection.sorted_reports(reports, project_start, zone)
    points = []
    for rep in seq:
        st = projection.project_state(
            plan, tasks, seq, at=rep["_dt"], tz=zone)
        points.append({
            "at": st["at"],
            "report_id": rep["report_id"],
            "critical_chain_completion_pct":
                st["critical_chain_completion_pct"],
            "buffer_consumed_pct": st["project_buffer"]["consumed_pct"],
            "zone": st["zone"],
            "tasks": st["tasks"],
        })
    return points
