"""Shared fixtures: the four-task example from the requirements."""
import pytest


def four_task_payload(capacity=1.0):
    return {
        "name": "demo",
        "start_time": "2026-01-05T09:00:00Z",  # a Monday
        "resources": [{"id": "eng", "name": "engineer",
                       "capacity": capacity}],
        "tasks": [
            {"id": "A", "name": "Spec", "predecessors": [],
             "demands": {"eng": 1}, "aggressive_days": 4,
             "conservative_days": 6},
            {"id": "B", "name": "Build-backend", "predecessors": ["A"],
             "demands": {"eng": 1}, "aggressive_days": 6,
             "conservative_days": 10},
            {"id": "C", "name": "Build-frontend", "predecessors": ["A"],
             "demands": {"eng": 1}, "aggressive_days": 3,
             "conservative_days": 5},
            {"id": "D", "name": "Release", "predecessors": ["B", "C"],
             "demands": {}, "aggressive_days": 2,
             "conservative_days": 4},
        ],
        "thresholds": {"yellow_pct": 50, "red_pct": 80},
    }


# Beijing (+08:00) regression example: conservative deltas sum to 7 so
# PB = 3.5, which is what produces the reported 31.7/36.5/25.4 control
# numbers.
BEIJING_REPORTS = [
    {"report_id": "r1", "task_id": "A", "at": "2026-01-05T09:00:00+08:00",
     "kind": "remaining", "remaining_days": 4},
    {"report_id": "r2", "task_id": "A", "at": "2026-01-09T10:00:00+08:00",
     "kind": "remaining", "remaining_days": 1},
    {"report_id": "r3", "task_id": "A", "at": "2026-01-09T16:00:00+08:00",
     "kind": "remaining", "remaining_days": 0.5},
    {"report_id": "r4", "task_id": "A", "at": "2026-01-09T17:00:00+08:00",
     "kind": "done"},
]


def beijing_payload(start: str, tz=None):
    body = {
        "name": "bj-demo",
        "start_time": start,
        "resources": [{"id": "eng", "name": "engineer", "capacity": 1}],
        "tasks": [
            {"id": "A", "name": "Spec", "predecessors": [],
             "demands": {"eng": 1}, "aggressive_days": 4,
             "conservative_days": 6},
            {"id": "B", "name": "Backend", "predecessors": ["A"],
             "demands": {"eng": 1}, "aggressive_days": 6,
             "conservative_days": 9},
            {"id": "C", "name": "Frontend", "predecessors": ["A"],
             "demands": {"eng": 1}, "aggressive_days": 3,
             "conservative_days": 4},
            {"id": "D", "name": "Release", "predecessors": ["B", "C"],
             "demands": {}, "aggressive_days": 2,
             "conservative_days": 3},
        ],
        "thresholds": {"yellow_pct": 50, "red_pct": 80},
    }
    if tz is not None:
        body["timezone"] = tz
    return body


@pytest.fixture
def payload():
    return four_task_payload()


@pytest.fixture
def planned(payload):
    from app.validation import validate_project
    from app.scheduler import build_plan
    validated = validate_project(payload)
    return validated, build_plan(validated)
