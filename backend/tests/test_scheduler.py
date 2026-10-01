"""Critical-chain / scheduler requirement tests."""
from app.scheduler import build_plan
from app.validation import validate_project

from conftest import four_task_payload


def test_four_task_example_makespan_15(planned):
    _validated, plan = planned
    assert plan["makespan_days"] == 15
    assert plan["critical_chain"] == ["A", "B", "C", "D"]
    # resource edge forces C after B
    assert ["B", "C"] in plan["resource_edges"]


def test_no_resource_conflict_equals_critical_path():
    from app.validation import validate_project as vp
    p = build_plan(vp(four_task_payload(capacity=2)))
    # dependency-only critical path is A-B-D = 4+6+2 = 12
    assert p["makespan_days"] == 12
    assert p["critical_chain"] == ["A", "B", "D"]


def test_project_buffer_from_duration_gap(planned):
    _, plan = planned
    # PB = 0.5 * ((6-4)+(10-6)+(5-3)+(4-2)) = 0.5 * 10 = 5
    assert plan["project_buffer"]["size_days"] == 5
    assert plan["project_buffer"]["start_day"] == 15
    assert plan["project_buffer"]["end_day"] == 20


def test_scaling_durations_scales_length_and_buffers_chain_unchanged():
    base = four_task_payload(capacity=1)
    p1 = build_plan(validate_project(base))
    k = 4
    scaled = four_task_payload(capacity=1)
    for t in scaled["tasks"]:
        t["aggressive_days"] *= k
        t["conservative_days"] *= k
    pk = build_plan(validate_project(scaled))
    assert pk["critical_chain"] == p1["critical_chain"]
    assert pk["makespan_days"] == p1["makespan_days"] * k
    assert pk["project_buffer"]["size_days"] == \
        p1["project_buffer"]["size_days"] * k


def test_deterministic_across_repeated_runs(payload):
    plans = [build_plan(validate_project(payload)) for _ in range(5)]
    chains = {tuple(p["critical_chain"]) for p in plans}
    assert len(chains) == 1
    starts = {tuple(sorted((t, round(s["start_day"], 9))
                            for t, s in p["schedule"].items()))
              for p in plans}
    assert len(starts) == 1


def test_feeding_buffer_on_noncritical_branch():
    # Two parallel independent branches merge into M.  The long branch is
    # the critical chain; the short branch carries a feeding buffer that
    # ends where M starts.
    payload = {
        "name": "feed",
        "start_time": "2026-01-05T09:00:00Z",
        "resources": [],
        "tasks": [
            {"id": "A", "name": "A", "predecessors": [], "demands": {},
             "aggressive_days": 10, "conservative_days": 14},
            {"id": "B", "name": "B", "predecessors": [], "demands": {},
             "aggressive_days": 4, "conservative_days": 8},
            {"id": "M", "name": "M", "predecessors": ["A", "B"],
             "demands": {}, "aggressive_days": 1, "conservative_days": 3},
        ],
        "thresholds": {"yellow_pct": 50, "red_pct": 80},
    }
    plan = build_plan(validate_project(payload))
    assert plan["critical_chain"] == ["A", "M"]
    assert plan["makespan_days"] == 11
    fbs = plan["feeding_buffers"]
    assert len(fbs) == 1
    fb = fbs[0]
    assert fb["chain"] == ["B"]
    assert fb["feeds_task"] == "M"
    # 0.5 * (8 - 4) = 2; buffer sits at the end of the B branch (day 4),
    # the remaining gap up to M (day 10) is schedule slack
    assert fb["size_days"] == 2
    assert fb["end_day"] == plan["schedule"]["B"]["finish_day"]
    assert fb["start_day"] == 2


def test_buffer_scaling_with_noninteger_k_is_proportional():
    base = four_task_payload(capacity=1)
    k = 7
    scaled = four_task_payload(capacity=1)
    for t in scaled["tasks"]:
        t["aggressive_days"] *= k
        t["conservative_days"] *= k
    pk = build_plan(validate_project(scaled))
    assert pk["project_buffer"]["size_days"] == 35
    assert pk["makespan_days"] == 105
