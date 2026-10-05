"""Regression tests for project working timezones.

The reported defect: all calendar math ran on the UTC wall clock, so a team
working 09:00-18:00 Beijing time that submitted +08:00 timestamps was
measured against a UTC work window -- late tasks showed zero buffer
consumption, and planned starts/finishes landed in the middle of the night
local time.
"""
from app import fever, projection
from app.scheduler import build_plan
from app.timezones import (UTC, derive_for_start, get_tz,
                           infer_timezone_name)
from app.validation import validate_project
from app.worktime import Calendar

from conftest import BEIJING_REPORTS, beijing_payload


def _pcts(plan, tasks, reports):
    return [p["buffer_consumed_pct"]
            for p in fever.trajectory(plan, tasks, reports)]


# --------------------------- zone resolution ----------------------------- #

def test_zone_derived_from_start_offset():
    name, tz = derive_for_start("2026-01-05T09:00:00+08:00")
    assert name == "Etc/GMT-8"
    assert tz.utcoffset(None).total_seconds() == 8 * 3600
    assert derive_for_start("2026-01-05T09:00:00Z")[0] == "UTC"
    assert derive_for_start("2026-01-05T09:00:00")[0] == "UTC"
    assert derive_for_start(None)[0] == "UTC"
    assert derive_for_start("2026-01-05T09:00:00-05:30")[0] == "UTC-05:30"


def test_explicit_timezone_field_persists():
    v = validate_project(
        beijing_payload("2026-01-05T09:00:00", tz="Asia/Shanghai"))
    assert v["timezone"] == "Asia/Shanghai"
    plan = build_plan(v)
    assert plan["timezone"] == "Asia/Shanghai"


def test_infer_timezone_from_stored_offsets():
    ats = ["2026-01-05T09:00:00+08:00", "2026-01-09T17:00:00+08:00"]
    assert infer_timezone_name("2026-01-05T09:00:00+08:00", ats) == "Etc/GMT-8"
    # pure-Z legacy data stays UTC
    assert infer_timezone_name(
        "2026-01-05T09:00:00Z",
        ["2026-01-05T09:00:00Z", "2026-01-09T17:00:00Z"]) == "UTC"
    # no offsets at all (naive stamps) -> UTC
    assert infer_timezone_name(None, ["2026-01-05T09:00:00"]) == "UTC"
    # mixed legacy data: start's own offset wins, deterministically
    assert infer_timezone_name(
        "2026-01-05T09:00:00+08:00",
        ["2026-01-09T17:00:00+00:00"]) == "Etc/GMT-8"


# --------------------------- calendar math ------------------------------- #

def test_plus_eight_calendar_reads_window_on_local_clock():
    cal = Calendar(get_tz("Etc/GMT-8"))
    mon = cal.parse_dt("2026-01-05T09:00:00+08:00")
    # A (4 workdays): Mon 09:00 -> Thu 18:00 Beijing; exactly inside window
    assert cal.work_add(mon, 4).isoformat() == "2026-01-08T18:00:00+08:00"
    assert cal.work_add(mon, 5).isoformat() == "2026-01-09T18:00:00+08:00"
    assert cal.work_add(mon, 6).isoformat() == "2026-01-12T18:00:00+08:00"


def test_naive_timestamp_attached_to_project_zone():
    cal = Calendar(get_tz("Asia/Shanghai"))
    aware = cal.parse_dt("2026-01-05T09:00:00+08:00")
    naive = cal.parse_dt("2026-01-05T09:00:00")
    assert naive == aware


# --------------------- the reported fever-chart defect ------------------- #

def test_beijing_fever_matches_z_control_point_by_point():
    v_bj = validate_project(beijing_payload("2026-01-05T09:00:00+08:00"))
    p_bj = build_plan(v_bj)
    v_z = validate_project(beijing_payload("2026-01-05T09:00:00Z"))
    p_z = build_plan(v_z)
    z_reports = [{**r, "at": r["at"].replace("+08:00", "+00:00")}
                 for r in BEIJING_REPORTS]

    bj = _pcts(p_bj, v_bj["tasks"], BEIJING_REPORTS)
    zc = _pcts(p_z, v_z["tasks"], z_reports)
    expected = [0.0, 31.746032, 36.507937, 25.396825]
    assert zc == expected
    assert bj == expected  # the bug gave 0 / 28.6 / 14.3 / 0 here


def test_planned_instants_fall_in_beijing_work_window():
    plan = build_plan(
        validate_project(beijing_payload("2026-01-05T09:00:00+08:00")))
    assert plan["project_start"] == "2026-01-05T09:00:00+08:00"
    a = plan["schedule"]["A"]
    assert a["start_at"] == "2026-01-05T09:00:00+08:00"
    assert a["finish_at"] == "2026-01-08T18:00:00+08:00"  # Thu, not 02:00 Fri


def test_iana_zone_with_naive_start_equals_offset_project():
    v_iana = validate_project(
        beijing_payload("2026-01-05T09:00:00", tz="Asia/Shanghai"))
    p_iana = build_plan(v_iana)
    naive_reports = [
        {"report_id": "r1", "task_id": "A", "at": "2026-01-05T09:00:00",
         "kind": "remaining", "remaining_days": 4},
        {"report_id": "r4", "task_id": "A", "at": "2026-01-09T17:00:00",
         "kind": "done"},
    ]
    v_off = validate_project(beijing_payload("2026-01-05T09:00:00+08:00"))
    p_off = build_plan(v_off)
    off_reports = [
        {"report_id": "r1", "task_id": "A", "at": "2026-01-05T09:00:00+08:00",
         "kind": "remaining", "remaining_days": 4},
        {"report_id": "r4", "task_id": "A", "at": "2026-01-09T17:00:00+08:00",
         "kind": "done"},
    ]
    assert (_pcts(p_iana, v_iana["tasks"], naive_reports)
            == _pcts(p_off, v_off["tasks"], off_reports))
    assert (p_iana["schedule"]["A"]["finish_at"]
            == "2026-01-08T18:00:00+08:00")


def test_history_replay_in_zone_matches_prefix_fold():
    v = validate_project(beijing_payload("2026-01-05T09:00:00+08:00"))
    plan = build_plan(v)
    traj = fever.trajectory(plan, v["tasks"], BEIJING_REPORTS)
    for point in traj:
        replay = projection.project_state(
            plan, v["tasks"], BEIJING_REPORTS, at=point["at"])
        assert replay["project_buffer"]["consumed_pct"] == \
            point["buffer_consumed_pct"]
        assert replay["at"] == point["at"]
        assert replay["tasks"] == point["tasks"]


def test_out_of_order_plus_eight_reports_match_ordered():
    v = validate_project(beijing_payload("2026-01-05T09:00:00+08:00"))
    plan = build_plan(v)
    shuffled = [BEIJING_REPORTS[i] for i in [3, 0, 2, 1]]
    assert projection.project_state(plan, v["tasks"], BEIJING_REPORTS) == \
        projection.project_state(plan, v["tasks"], shuffled)


def test_utc_project_numbers_unchanged_from_calendar_refactor():
    # Locks the "pure-Z projects must not move" guarantee at the unit level.
    cal = Calendar(UTC)
    mon = cal.parse_dt("2026-01-05T09:00:00Z")
    assert cal.work_add(mon, 1).isoformat() == "2026-01-05T18:00:00+00:00"
    v = validate_project(beijing_payload("2026-01-05T09:00:00Z"))
    plan = build_plan(v)
    assert plan["timezone"] == "UTC"
    assert plan["project_start"] == "2026-01-05T09:00:00+00:00"
    z_reports = [{**r, "at": r["at"].replace("+08:00", "+00:00")}
                 for r in BEIJING_REPORTS]
    assert _pcts(plan, v["tasks"], z_reports) == \
        [0.0, 31.746032, 36.507937, 25.396825]
