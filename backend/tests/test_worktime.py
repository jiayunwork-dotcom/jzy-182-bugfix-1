"""Working calendar sanity checks (Mon-Fri, 09:00-18:00)."""
from datetime import datetime

from app.worktime import parse_dt, work_add, work_offset

MON = "2026-01-05T09:00:00Z"


def test_one_day():
    assert work_add(parse_dt(MON), 1) == parse_dt("2026-01-05T18:00:00Z")


def test_crosses_weekend():
    # Mon-Fri fill exactly 5 workdays -> Friday 18:00; one more -> Mon 18:00
    assert work_add(parse_dt(MON), 5) == parse_dt("2026-01-09T18:00:00Z")
    assert work_add(parse_dt(MON), 6) == parse_dt("2026-01-12T18:00:00Z")


def test_weekend_start_snaps_forward():
    sat = "2026-01-10T12:00:00Z"
    assert work_add(parse_dt(sat), 0) == parse_dt("2026-01-12T09:00:00Z")


def test_offset_roundtrip():
    base = parse_dt(MON)
    for days in [0.0, 0.25, 1.0, 4.9, 7.0, 12.5]:
        assert abs(work_offset(base, work_add(base, days)) - days) < 1e-9


def test_negative_offset():
    fri_eve = parse_dt("2026-01-09T18:00:00Z")
    assert work_offset(parse_dt(MON), fri_eve) == 5
    mon_eve = parse_dt("2026-01-05T18:00:00Z")
    assert work_offset(parse_dt("2026-01-06T09:00:00Z"), mon_eve) == -1
