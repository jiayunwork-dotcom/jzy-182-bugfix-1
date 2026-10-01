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


@pytest.fixture
def payload():
    return four_task_payload()


@pytest.fixture
def planned(payload):
    from app.validation import validate_project
    from app.scheduler import build_plan
    validated = validate_project(payload)
    return validated, build_plan(validated)
