"""Exact regression for the reported Beijing fever-chart bug.

The four-task minimum example (B and C contest one engineer) created with
a +08:00 start, reports carrying +08:00 wall-clock moments.  Before the
fix the four points read 0 / 28.6 / 14.3 / 0 % -- the chart got *greener*
as the task slipped, and returned to zero after a late completion.  The
Z control group on identical wall-clock moments read 0 / 31.7 / 36.5 /
25.4 %, which is the correct reading; the +08:00 project must match it.
"""
import pytest

from app import fever, projection
from app.scheduler import build_plan
from app.validation import validate_project


def _payload(timezone, start_time):
    return {
        "name": "min4",
        "timezone": timezone,
        "start_time": start_time,
        "resources": [{"id": "eng", "name": "engineer", "capacity": 1}],
        "tasks": [
            {"id": "A", "name": "A", "predecessors": [],
             "demands": {"eng": 1}, "aggressive_days": 4,
             "conservative_days": 5},
            {"id": "B", "name": "B", "predecessors": ["A"],
             "demands": {"eng": 1}, "aggressive_days": 6,
             "conservative_days": 9},
            {"id": "C", "name": "C", "predecessors": ["A"],
             "demands": {"eng": 1}, "aggressive_days": 3,
             "conservative_days": 5},
            {"id": "D", "name": "D", "predecessors": ["B", "C"],
             "demands": {}, "aggressive_days": 2,
             "conservative_days": 3},
        ],
        "thresholds": {"yellow_pct": 50, "red_pct": 80},
    }


REPORTS = [
    ("r1", "2026-01-05T09:00:00", "remaining", 4),    # Mon 09:00
    ("r2", "2026-01-09T10:00:00", "remaining", 1),    # Fri 10:00
    ("r3", "2026-01-09T16:00:00", "remaining", 0.5),  # Fri 16:00
    ("r4", "2026-01-09T17:00:00", "done", None),      # Fri 17:00
]
EXPECTED = [0.0, 31.746032, 36.507937, 25.396825]


def _reports(suffix):
    out = []
    for rid, wall, kind, rem in REPORTS:
        r = {"report_id": rid, "task_id": "A", "at": wall + suffix,
             "kind": kind}
        if rem is not None:
            r["remaining_days"] = rem
        out.append(r)
    return out


def _run(timezone, start_time, suffix):
    v = validate_project(_payload(timezone, start_time))
    plan = build_plan(v)
    points = fever.trajectory(plan, v["tasks"], _reports(suffix),
                              tz=v["timezone"])
    return v, plan, points


def test_beijing_project_matches_z_control_point_by_point():
    _, plan_bj, pts_bj = _run(
        "Asia/Shanghai", "2026-01-05T09:00:00+08:00", "+08:00")
    _, plan_z, pts_z = _run("UTC", "2026-01-05T09:00:00Z", "Z")
    assert [p["buffer_consumed_pct"] for p in pts_bj] == EXPECTED
    assert [p["buffer_consumed_pct"] for p in pts_z] == EXPECTED


def test_beijing_planned_moments_are_within_working_hours():
    _, plan, _ = _run("Asia/Shanghai", "2026-01-05T09:00:00+08:00", "+08:00")
    assert plan["schedule"]["A"]["start_at"] == "2026-01-05T09:00:00+08:00"
    # 4 aggressive workdays from Monday 09:00 -> Thursday 18:00, same week
    assert plan["schedule"]["A"]["finish_at"] == \
        "2026-01-08T18:00:00+08:00"
    # D ends the 15-workday chain on a Friday at 18:00 local, never at night
    assert plan["schedule"]["D"]["finish_at"] == \
        "2026-01-23T18:00:00+08:00"


def test_late_finish_stays_consumed_does_not_return_to_zero():
    """The exact symptom: after 'done' on Friday 17:00, PB must show 25.4%."""
    v, plan, _ = _run("Asia/Shanghai", "2026-01-05T09:00:00+08:00", "+08:00")
    final = projection.project_state(
        plan, v["tasks"], _reports("+08:00"), tz="Asia/Shanghai")
    a = next(t for t in final["tasks"] if t["task_id"] == "A")
    assert a["status"] == "done"
    assert final["project_buffer"]["consumed_pct"] == 25.396825
    # A finished ~0.89 workdays late (planned Thu 18:00 vs actual Fri 17:00)
    assert abs(a["actual_finish_day"] - 4.888889) < 1e-6


def test_fixed_offset_and_iana_equivalent_for_china():
    _, p1, pts1 = _run("+08:00", "2026-01-05T09:00:00+08:00", "+08:00")
    _, p2, pts2 = _run("Asia/Shanghai", "2026-01-05T09:00:00+08:00",
                       "+08:00")
    assert [p["buffer_consumed_pct"] for p in pts1] == \
           [p["buffer_consumed_pct"] for p in pts2]


def test_naive_timestamps_in_project_zone_identical_to_offset_ones():
    _, _, pts_naive = _run(
        "Asia/Shanghai", "2026-01-05T09:00:00", "")
    assert [p["buffer_consumed_pct"] for p in pts_naive] == EXPECTED
