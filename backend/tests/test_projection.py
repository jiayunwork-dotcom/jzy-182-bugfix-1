"""Report-stream projection tests.

Timestamps use the project's Monday 2026-01-05 09:00 start; a moment
``n`` workdays later is built with work_add so weekends are respected.
"""
from app import projection, fever
from app.worktime import work_add, parse_dt, parse_dt as p


def at(start, days, hour=18):
    dt = work_add(parse_dt(start), days)
    return dt.isoformat()


def _feed(validated, plan, reports, cutoff=None):
    return projection.project_state(
        plan, validated["tasks"], reports, at=cutoff)


def test_empty_project_state(planned):
    v, plan = planned
    st = _feed(v, plan, [])
    assert st["critical_chain_completion_pct"] == 0
    assert st["project_buffer"]["consumed_pct"] == 0
    assert st["zone"] == "green"


def test_on_time_completion_consumes_no_buffer(planned):
    v, plan = planned
    start = plan["project_start"]
    # complete each CC task exactly at its planned finish: 4,10,13,15
    reports = [
        {"report_id": "r1", "task_id": "A", "at": at(start, 4),
         "kind": "done"},
        {"report_id": "r2", "task_id": "B", "at": at(start, 10),
         "kind": "done"},
        {"report_id": "r3", "task_id": "C", "at": at(start, 13),
         "kind": "done"},
        {"report_id": "r4", "task_id": "D", "at": at(start, 15),
         "kind": "done"},
    ]
    st = _feed(v, plan, reports)
    assert st["critical_chain_completion_pct"] == 100
    assert st["project_buffer"]["consumed_pct"] == 0
    assert st["zone"] == "green"


def test_late_task_consumes_buffer(planned):
    v, plan = planned
    start = plan["project_start"]
    # A finishes 2 workdays late -> pushes the chain end by 2 of PB 5
    reports = [
        {"report_id": "r1", "task_id": "A", "at": at(start, 6),
         "kind": "done"},
    ]
    st = _feed(v, plan, reports)
    assert st["project_buffer"]["consumed_days"] == 2
    assert st["project_buffer"]["consumed_pct"] == 40
    # yellow threshold is 50%, so 40% stays green
    assert st["zone"] == "green"


def test_out_of_order_arrivals_match_ordered(planned):
    v, plan = planned
    start = plan["project_start"]
    ordered = [
        {"report_id": "r1", "task_id": "A", "at": at(start, 4),
         "kind": "done"},
        {"report_id": "r2", "task_id": "B", "at": at(start, 10),
         "kind": "remaining", "remaining_days": 2},
        {"report_id": "r3", "task_id": "B", "at": at(start, 11),
         "kind": "done"},
        {"report_id": "r4", "task_id": "C", "at": at(start, 13),
         "kind": "done"},
        {"report_id": "r5", "task_id": "D", "at": at(start, 16),
         "kind": "done"},
    ]
    shuffled = [ordered[i] for i in [4, 1, 0, 3, 2]]
    s_order = _feed(v, plan, ordered)
    s_shuf = _feed(v, plan, shuffled)
    assert s_order == s_shuf


def test_earlier_report_inserted_and_recomputed(planned):
    """A late-arriving older report is inserted back, not rejected."""
    v, plan = planned
    start = plan["project_start"]
    # at day 8 B says 5 remaining; at day 6 (arrives later) B said 6 left
    late = [
        {"report_id": "later", "task_id": "B", "at": at(start, 8),
         "kind": "remaining", "remaining_days": 5},
    ]
    with_older = late + [
        {"report_id": "earlier", "task_id": "B", "at": at(start, 6),
         "kind": "remaining", "remaining_days": 6},
    ]
    st = _feed(v, plan, with_older)
    # latest-in-time at day 8 still wins for status
    b = next(t for t in st["tasks"] if t["task_id"] == "B")
    assert b["status"] == "in_progress"
    assert b["remaining_days"] == 5
    assert b["last_report_id"] == "later"


def test_same_timestamp_tiebreak_by_report_id(planned):
    v, plan = planned
    start = plan["project_start"]
    ts = at(start, 7)
    r_aa = {"report_id": "aa", "task_id": "B", "at": ts,
            "kind": "remaining", "remaining_days": 3}
    r_bb = {"report_id": "bb", "task_id": "B", "at": ts,
            "kind": "remaining", "remaining_days": 1}
    s1 = _feed(v, plan, [r_bb, r_aa])
    s2 = _feed(v, plan, [r_aa, r_bb])
    assert s1 == s2
    b = next(t for t in s1["tasks"] if t["task_id"] == "B")
    # 'bb' sorts after 'aa' -> its remaining value wins
    assert b["remaining_days"] == 1


def test_history_replay_matches_prefix_fold(planned):
    v, plan = planned
    start = plan["project_start"]
    reports = [
        {"report_id": "r1", "task_id": "A", "at": at(start, 4),
         "kind": "done"},
        {"report_id": "r2", "task_id": "B", "at": at(start, 12),
         "kind": "done"},
        {"report_id": "r3", "task_id": "C", "at": at(start, 13),
         "kind": "done"},
    ]
    traj = fever.trajectory(plan, v["tasks"], reports)
    for point in traj:
        replay = _feed(v, plan, reports, cutoff=point["at"])
        assert replay["critical_chain_completion_pct"] == \
            point["critical_chain_completion_pct"]
        assert replay["project_buffer"]["consumed_pct"] == \
            point["buffer_consumed_pct"]
        assert replay["zone"] == point["zone"]
        assert replay["tasks"] == point["tasks"]
        assert replay["at"] == point["at"]


def test_fever_red_zone_after_buffer_exhausted(planned):
    v, plan = planned
    start = plan["project_start"]
    # PB = 5; A finishing 6 days late -> >100% buffer -> red
    reports = [{"report_id": "r1", "task_id": "A", "at": at(start, 10),
                "kind": "done"}]
    st = _feed(v, plan, reports)
    assert st["project_buffer"]["consumed_pct"] == 120
    assert st["zone"] == "red"


def test_same_timestamp_points_replay_consistently(planned):
    """Two reports at one instant: trajectory point == /state?at=instant."""
    v, plan = planned
    start = plan["project_start"]
    ts = at(start, 8)
    reports = [
        {"report_id": "b", "task_id": "A", "at": ts,
         "kind": "remaining", "remaining_days": 1},
        {"report_id": "a", "task_id": "B", "at": ts,
         "kind": "remaining", "remaining_days": 2},
    ]
    traj = fever.trajectory(plan, v["tasks"], reports)
    replayed = _feed(v, plan, reports, cutoff=ts)
    # both points describe the same instant state
    assert len(traj) == 2
    for point in traj:
        assert point["at"] == replayed["at"]
        assert point["tasks"] == replayed["tasks"]
        assert point["buffer_consumed_pct"] == \
            replayed["project_buffer"]["consumed_pct"]
    # listing order follows (at, report_id)
    assert [p["report_id"] for p in traj] == ["a", "b"]


def test_critical_chain_frozen_during_execution(planned):
    v, plan = planned
    start = plan["project_start"]
    # C (non... actually CC here) races ahead and B lags badly; the frozen
    # chain stays the planned chain regardless.
    original = list(plan["critical_chain"])
    reports = [
        {"report_id": "r1", "task_id": "C", "at": at(start, 10),
         "kind": "done"},
        {"report_id": "r2", "task_id": "B", "at": at(start, 14),
         "kind": "remaining", "remaining_days": 4},
    ]
    st = _feed(v, plan, reports)
    assert plan["critical_chain"] == original
    # state never mutates the plan
    assert all("chain" not in t for t in st["tasks"])
