"""Working-time calendar.

One *workday* is 09:00-18:00 (9 work hours) on a Monday-Friday; weekends
carry no work.  This simplification (no holidays, fixed 9h window) is part
of the documented rules (see docs/DECISIONS.md).

The 09:00-18:00 window is read on the wall clock of one specific zone
(:class:`Calendar`, bound to a project's timezone).  All weekday/hour logic
runs on local wall-clock fields; only comparisons use the absolute instant.
This is what makes a ``+08:00`` project that opens Monday 09:00 Beijing time
actually start Monday 09:00 Beijing time rather than 09:00 UTC.

All durations inside the scheduler are expressed in workdays as plain
floats; these helpers only translate between absolute timestamps and
"workdays elapsed since the project start".

Naive timestamps (no offset) are interpreted in the bound zone.  The
module-level functions keep their historical behavior: naive means UTC and
results are rendered in UTC -- they are convenience wrappers around a UTC
calendar.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone, tzinfo

UTC = timezone.utc

WORK_START_HOUR = 9
WORK_END_HOUR = 18
_WORK_SECONDS = (WORK_END_HOUR - WORK_START_HOUR) * 3600


class Calendar:
    """A Mon-Fri 09:00-18:00 calendar read in one fixed zone."""

    def __init__(self, tz: tzinfo):
        self.tz = tz

    def parse_dt(self, value: str | datetime) -> datetime:
        """Parse ISO to an aware instant expressed in this zone.

        Naive values are *attached* to this zone (they name a wall-clock
        moment in it); aware values are converted into it.
        """
        if isinstance(value, datetime):
            dt = value
        else:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=self.tz)
        return dt.astimezone(self.tz)

    def to_iso(self, dt: datetime) -> str:
        return self.parse_dt(dt).isoformat()

    @staticmethod
    def _is_workday(dt: datetime) -> bool:
        return dt.weekday() < 5

    @staticmethod
    def _fraction(dt: datetime) -> float:
        """Elapsed fraction of the working window for a weekday moment."""
        seconds = (dt.hour * 3600 + dt.minute * 60 + dt.second
                   + dt.microsecond / 1_000_000
                   - WORK_START_HOUR * 3600)
        return min(1.0, max(0.0, seconds / _WORK_SECONDS))

    def snap_forward(self, dt: datetime) -> datetime:
        """Snap a moment forward into the next valid working instant.

        18:00 sharp is the (valid) end of the working window; only moments
        strictly after it roll to the next morning.
        """
        dt = self.parse_dt(dt)
        while not self._is_workday(dt):
            dt = (dt + timedelta(days=1)).replace(hour=WORK_START_HOUR,
                                                  minute=0, second=0,
                                                  microsecond=0)
        if dt.hour < WORK_START_HOUR:
            return dt.replace(hour=WORK_START_HOUR, minute=0, second=0,
                              microsecond=0)
        if dt.hour > WORK_END_HOUR or (dt.hour == WORK_END_HOUR
                                       and (dt.minute or dt.second
                                            or dt.microsecond)):
            nxt = dt + timedelta(days=1)
            return self.snap_forward(
                nxt.replace(hour=WORK_START_HOUR, minute=0, second=0,
                            microsecond=0))
        return dt

    def forward_workdays(self, a: datetime, b: datetime) -> float:
        """Work capacity between two moments (``b >= a``), snapped to window."""
        a, b = self.snap_forward(a), self.snap_forward(b)
        if b < a:
            raise ValueError("forward_workdays requires b >= a")
        # Walk dates: a's own date contributes (1-frac(a)); each crossed
        # boundary moves to the next morning, the final date contributes
        # frac(b)-frac(cur).
        total = 0.0
        cur = a
        while cur.date() < b.date():
            if self._fraction(cur) >= 1.0:
                total += 1.0  # started exactly at a day's end
            else:
                total += 1.0 - self._fraction(cur)
            cur = self.snap_forward(
                cur.replace(hour=WORK_START_HOUR, minute=0, second=0,
                            microsecond=0) + timedelta(days=1))
        total += self._fraction(b) - self._fraction(cur)
        return total

    def work_offset(self, base: datetime, at: datetime) -> float:
        """Signed workdays from ``base`` to ``at`` (negative if earlier)."""
        base, at = self.parse_dt(base), self.parse_dt(at)
        if at >= base:
            return self.forward_workdays(base, at)
        return -self.forward_workdays(at, base)

    def work_add(self, base: datetime, days: float) -> datetime:
        """Move ``days`` workdays forward from ``base`` (days >= 0)."""
        cur = self.snap_forward(base)
        remaining = float(days)
        # first (possibly partial) day: fill from the current window fraction
        room = 1.0 - self._fraction(cur)
        if remaining <= room + 1e-12:
            return cur + timedelta(seconds=remaining * _WORK_SECONDS)
        remaining -= room
        cur = self.snap_forward(
            cur.replace(hour=WORK_START_HOUR, minute=0, second=0,
                        microsecond=0) + timedelta(days=1))
        while remaining > 1.0 + 1e-12:
            remaining -= 1.0
            cur = self.snap_forward(
                cur.replace(hour=WORK_START_HOUR, minute=0, second=0,
                            microsecond=0) + timedelta(days=1))
        return cur + timedelta(seconds=remaining * _WORK_SECONDS)


# ------------------------- UTC convenience wrappers ---------------------- #
# These preserve the pre-timezone module API exactly: naive timestamps mean
# UTC and every aware timestamp is converted to UTC before any arithmetic.

_UTC_CALENDAR = Calendar(UTC)


def parse_dt(value: str | datetime) -> datetime:
    """Parse an ISO timestamp; naive values are assumed to be UTC."""
    return _UTC_CALENDAR.parse_dt(value)


def to_iso(dt: datetime) -> str:
    return _UTC_CALENDAR.to_iso(dt)


def snap_forward(dt: datetime) -> datetime:
    return _UTC_CALENDAR.snap_forward(dt)


def forward_workdays(a: datetime, b: datetime) -> float:
    return _UTC_CALENDAR.forward_workdays(a, b)


def work_offset(base: datetime, at: datetime) -> float:
    return _UTC_CALENDAR.work_offset(base, at)


def work_add(base: datetime, days: float) -> datetime:
    return _UTC_CALENDAR.work_add(base, days)
