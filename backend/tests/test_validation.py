"""Input rejection tests: each must name the offending field."""
import copy

import pytest

from app.validation import ValidationIssue, validate_project
from conftest import four_task_payload


def expect_field(payload, field_fragment):
    with pytest.raises(ValidationIssue) as exc:
        validate_project(payload)
    assert field_fragment in exc.value.field, exc.value.field
    return exc.value.message


def test_cycle_rejected(payload):
    payload["tasks"][0]["predecessors"] = ["D"]  # A<-D<-B<-A
    msg = expect_field(payload, "predecessors")
    assert "cycle" in msg


def test_unknown_predecessor(payload):
    payload["tasks"][2]["predecessors"].append("ZZZ")
    expect_field(payload, "predecessors")


def test_self_dependency(payload):
    payload["tasks"][1]["predecessors"].append("B")
    expect_field(payload, "predecessors")


def test_unknown_resource_in_demands(payload):
    payload["tasks"][1]["demands"]["ghost"] = 1
    expect_field(payload, "demands.ghost")


def test_demand_exceeds_capacity(payload):
    payload["tasks"][1]["demands"]["eng"] = 5
    expect_field(payload, "demands.eng")


def test_negative_aggressive_duration(payload):
    payload["tasks"][0]["aggressive_days"] = -1
    expect_field(payload, "aggressive_days")


def test_negative_conservative_duration(payload):
    payload["tasks"][0]["conservative_days"] = -2
    expect_field(payload, "conservative_days")


def test_aggressive_greater_than_conservative(payload):
    payload["tasks"][0]["aggressive_days"] = 9
    payload["tasks"][0]["conservative_days"] = 4
    expect_field(payload, "aggressive_days")


@pytest.mark.parametrize("y,r", [(-1, 80), (101, 120), (80, 80),
                                 (90, 50), (0, 0)])
def test_threshold_ranges(payload, y, r):
    payload["thresholds"] = {"yellow_pct": y, "red_pct": r}
    expect_field(payload, "thresholds")


def test_duplicate_task_id(payload):
    dup = copy.deepcopy(payload["tasks"][0])
    payload["tasks"].append(dup)
    expect_field(payload, "tasks[4].id")


def test_empty_tasks(payload):
    payload["tasks"] = []
    expect_field(payload, "tasks")
