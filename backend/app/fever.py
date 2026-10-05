"""Fever-chart queries.

A fever point is the projected state *at a report instant*; querying the
state at that instant reproduces every field of the point, so history is a
pure replay (prefix fold) -- no snapshots are stored.
"""
from __future__ import annotations

from . import projection
from .projection import plan_calendar


def trajectory(plan: dict, tasks: dict, reports: list[dict]) -> list[dict]:
    """One point per report in the total order (at, report_id).

    A point equals the state reached with every report whose timestamp is
    <= the point timestamp, so two reports sharing one instant describe the
    same unique state at that instant; the report_id tiebreak only fixes
    their listing order.
    """
    cal = plan_calendar(plan)
    project_start = cal.parse_dt(plan["project_start"])
    seq = projection.sorted_reports(reports, project_start, cal)
    points = []
    for rep in seq:
        st = projection.project_state(plan, tasks, seq, at=rep["_dt"])
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
