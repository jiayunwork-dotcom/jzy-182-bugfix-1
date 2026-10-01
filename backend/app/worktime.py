"""Working-time calendar.

One *workday* is 09:00-18:00 (9 work hours) on a Monday-Friday; weekends
carry no work.  This simplification (no holidays, fixed 9h window) is part
of the documented rules (see docs/DECISIONS.md).

All durations inside the scheduler are expressed in workdays as plain
floats; these helpers only translate between absolute timestamps and
"workdays elapsed since the project start".
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

UTC = timezone.utc

WORK_START_HOUR = 9
WORK_END_HOUR = 18
_WORK_SECONDS = (WORK_END_HOUR - WORK_START_HOUR) * 3600


def parse_dt(value: str | datetime) -> datetime:
    """Parse an ISO timestamp; naive values are assumed to be UTC."""
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def to_iso(dt: datetime) -> str:
    return parse_dt(dt).isoformat()


def _is_workday(dt: datetime) -> bool:
    return dt.weekday() < 5


def _fraction(dt: datetime) -> float:
    """Elapsed fraction of the working window for a weekday moment."""
    seconds = (dt.hour * 3600 + dt.minute * 60 + dt.second
               + dt.microsecond / 1_000_000
               - WORK_START_HOUR * 3600)
    return min(1.0, max(0.0, seconds / _WORK_SECONDS))


def snap_forward(dt: datetime) -> datetime:
    """Snap a moment forward into the next valid working instant.

    18:00 sharp is the (valid) end of the working window; only moments
    strictly after it roll to the next morning.
    """
    dt = parse_dt(dt)
    while not _is_workday(dt):
        dt = (dt + timedelta(days=1)).replace(hour=WORK_START_HOUR,
                                              minute=0, second=0, microsecond=0)
    if dt.hour < WORK_START_HOUR:
        return dt.replace(hour=WORK_START_HOUR, minute=0, second=0,
                          microsecond=0)
    if dt.hour > WORK_END_HOUR or (dt.hour == WORK_END_HOUR
                                   and (dt.minute or dt.second or dt.microsecond)):
        nxt = dt + timedelta(days=1)
        return snap_forward(nxt.replace(hour=WORK_START_HOUR, minute=0,
                                        second=0, microsecond=0))
    return dt


def forward_workdays(a: datetime, b: datetime) -> float:
    """Work capacity between two moments (``b >= a``), snapped to window."""
    a, b = snap_forward(a), snap_forward(b)
    if b < a:
        raise ValueError("forward_workdays requires b >= a")
    # Walk dates: a's own date contributes (1-frac(a)) per crossed boundary,
    # the final date contributes frac(b)-frac(cur).
    total = 0.0
    cur = a
    while cur.date() < b.date():
        if _fraction(cur) >= 1.0:
            total += 1.0  # started exactly at a day's end
        else:
            total += 1.0 - _fraction(cur)
        cur = snap_forward(cur.replace(hour=WORK_START_HOUR, minute=0,
                                       second=0, microsecond=0)
                           + timedelta(days=1))
    total += _fraction(b) - _fraction(cur)
    return total


def work_offset(base: datetime, at: datetime) -> float:
    """Signed workdays from ``base`` to ``at`` (negative when ``at`` earlier)."""
    base, at = parse_dt(base), parse_dt(at)
    if at >= base:
        return forward_workdays(base, at)
    return -forward_workdays(at, base)


def work_add(base: datetime, days: float) -> datetime:
    """Move ``days`` workdays forward from ``base`` (days >= 0)."""
    cur = snap_forward(base)
    remaining = float(days)
    # first (possibly partial) day: fill from the current window fraction
    room = 1.0 - _fraction(cur)
    if remaining <= room + 1e-12:
        return cur + timedelta(seconds=remaining * _WORK_SECONDS)
    remaining -= room
    cur = snap_forward(cur.replace(hour=WORK_START_HOUR, minute=0, second=0,
                                   microsecond=0) + timedelta(days=1))
    while remaining > 1.0 + 1e-12:
        remaining -= 1.0
        cur = snap_forward(cur.replace(hour=WORK_START_HOUR, minute=0,
                                       second=0, microsecond=0)
                           + timedelta(days=1))
    return cur + timedelta(seconds=remaining * _WORK_SECONDS)
