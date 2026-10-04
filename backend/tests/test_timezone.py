"""Project-timezone calendar tests.

Regression for the bug where every timestamp was converted to UTC before
applying the Monday-Friday 09:00-18:00 calendar: a Beijing team recording
``+08:00`` wall-clock moments was effectively working on the UTC clock,
so daytime reports landed in the UTC night/weekend and buffer penetration
came out wrong (even going back to 0% after a late finish).

The invariant the fix rests on: the same *local wall-clock* moments must
map to the same work offsets in whatever timezone the project declares,
and a UTC project must keep its old numbers exactly.
"""
from app.worktime import (parse_dt, work_add, work_offset, resolve_timezone,
                          offset_spec, snap_forward)

MON_BJ = "2026-01-05T09:00:00+08:00"  # Monday 09:00 Beijing


def test_weekday_window_evalued_in_project_timezone():
    # Monday 09:00 Beijing == Monday 01:00 UTC: on the UTC calendar the
    # same instant is *before* the working window; in +08:00 it is 09:00.
    bj = resolve_timezone("+08:00")
    assert snap_forward(MON_BJ, bj) == parse_dt(MON_BJ, bj)
    assert snap_forward(MON_BJ, "UTC") == parse_dt("2026-01-05T09:00:00Z")


def test_naive_timestamp_is_project_wall_time():
    # No offset -> local wall clock in the project timezone.
    assert parse_dt("2026-01-05T09:00:00", "Asia/Shanghai") == \
        parse_dt("2026-01-05T09:00:00+08:00", "Asia/Shanghai")


def test_explicit_offset_names_an_instant():
    # 09:00+08 and 01:00Z are the same instant in every timezone.
    assert parse_dt("2026-01-05T09:00:00+08:00", "Asia/Shanghai") == \
        parse_dt("2026-01-05T01:00:00Z", "UTC")


def test_full_workweek_in_beijing():
    bj = resolve_timezone("Asia/Shanghai")
    start = parse_dt(MON_BJ, bj)
    # 4 aggressive workdays from Monday 09:00 -> Thursday 18:00 local
    assert work_add(start, 4, bj) == parse_dt("2026-01-08T18:00:00+08:00", bj)
    assert work_add(start, 5, bj) == parse_dt("2026-01-09T18:00:00+08:00", bj)
    assert work_add(start, 6, bj) == parse_dt("2026-01-12T18:00:00+08:00", bj)


def test_offsets_identical_for_same_wall_clock_in_any_zone():
    """The core fix: +08 project on +08 clock == Z project on UTC clock."""
    bj = resolve_timezone("+08:00")
    utc = resolve_timezone("UTC")
    moments = [(0.0, "2026-01-05T09:00:00"),
               (0.111111, "2026-01-05T10:00:00"),
               (4.0, "2026-01-08T18:00:00"),
               (4.888889, "2026-01-09T17:00:00")]
    base_bj = parse_dt("2026-01-05T09:00:00+08:00", bj)
    base_z = parse_dt("2026-01-05T09:00:00Z", utc)
    for i, (_day, wall) in enumerate(moments):
        off_bj = work_offset(base_bj, wall + "+08:00", bj)
        off_z = work_offset(base_z, wall + "Z", utc)
        assert abs(off_bj - off_z) < 1e-9, (i, off_bj, off_z)


def test_naive_roundtrip_and_negative_in_beijing():
    bj = resolve_timezone("Asia/Shanghai")
    base = parse_dt("2026-01-05T09:00:00", bj)
    for days in [0.0, 0.25, 1.0, 4.9, 7.0, 12.5]:
        assert abs(work_offset(base, work_add(base, days, bj), bj) - days) \
            < 1e-9
    fri_eve = parse_dt("2026-01-09T18:00:00", bj)
    assert work_offset(base, fri_eve, bj) == 5


def test_resolve_timezone_forms():
    assert resolve_timezone("UTC") is resolve_timezone("Z")
    assert offset_spec(resolve_timezone("+08:00")) == "+08:00"
    assert offset_spec(resolve_timezone("UTC-05:30")) == "-05:30"
    assert offset_spec(resolve_timezone("Asia/Shanghai")) in (
        "+08:00",)  # China has no DST
    assert offset_spec(resolve_timezone(None)) == "UTC"


def test_unknown_timezone_rejected():
    import pytest
    with pytest.raises(ValueError):
        resolve_timezone("Asia/NoSuchCity")
    with pytest.raises(ValueError):
        resolve_timezone("+25:00")


def test_friday_night_local_is_not_snapped_to_monday():
    # 17:00 Beijing Friday must remain 17:00, not roll as 09:00 UTC
    # Saturday -> Monday.
    bj = resolve_timezone("+08:00")
    dt = snap_forward("2026-01-09T17:00:00+08:00", bj)
    assert dt == parse_dt("2026-01-09T17:00:00+08:00", bj)
    assert dt.weekday() == 4
