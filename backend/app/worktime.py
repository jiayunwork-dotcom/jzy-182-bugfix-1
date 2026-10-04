"""Working-time calendar.

One *workday* is 09:00-18:00 (9 work hours) on a Monday-Friday; weekends
carry no work.  This simplification (no holidays, fixed 9h window) is part
of the documented rules (see docs/DECISIONS.md).

All durations inside the scheduler are expressed in workdays as plain
floats; these helpers only translate between absolute timestamps and
"workdays elapsed since the project start".

Timezone model (see docs/DECISIONS.md, section 6):

* Every project has a timezone (an IANA name like ``Asia/Shanghai`` or a
  fixed offset like ``+08:00``).  The calendar -- weekday boundaries and
  the 09:00-18:00 window -- is evaluated on **local wall-clock** time in
  that timezone.
* Timestamps carrying an explicit offset (``Z`` / ``+08:00``) always mean
  the absolute instant they state; naive timestamps are assumed to be
  wall-clock time in the project timezone.
* With timezone UTC this module is byte-for-byte compatible with the old
  UTC-only behaviour.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone, tzinfo

try:  # Python 3.9+: system tzdata, or the ``tzdata`` pip package
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:  # pragma: no cover
    ZoneInfo = None

    class ZoneInfoNotFoundError(Exception):
        pass

UTC = timezone.utc

WORK_START_HOUR = 9
WORK_END_HOUR = 18
_WORK_SECONDS = (WORK_END_HOUR - WORK_START_HOUR) * 3600

_OFFSET_RE = re.compile(r"([+-])(\d{1,2})(?::?(\d{2}))?$")
_PREFIXES = ("UTC", "GMT")


def resolve_timezone(spec: str | tzinfo | None) -> tzinfo:
    """Resolve a timezone spec to a tzinfo.

    Accepts ``None``/``UTC``/``Z``, fixed offsets (``+08:00``, ``-0530``,
    ``UTC+05:30``) and IANA names (``Asia/Shanghai``).  Raises ValueError
    for anything else.
    """
    if spec is None:
        return UTC
    if isinstance(spec, tzinfo):
        return spec
    s = str(spec).strip()
    if s == "" or s in ("Z", "z"):
        return UTC
    upper = s.upper()
    if upper in ("UTC", "GMT"):
        return UTC
    body = s
    for prefix in _PREFIXES:
        if upper.startswith(prefix) and len(s) > len(prefix) \
                and s[len(prefix)] in "+-":
            body = s[len(prefix):]
            break
    m = _OFFSET_RE.fullmatch(body)
    if m:
        sign, hours, minutes = m.groups()
        mins = int(hours) * 60 + int(minutes or 0)
        if int(hours) > 23 or int(minutes or 0) > 59:
            raise ValueError(f"invalid timezone offset {spec!r}")
        if sign == "-":
            mins = -mins
        return timezone(timedelta(minutes=mins))
    if ZoneInfo is None:  # pragma: no cover
        raise ValueError(f"named timezones are not supported: {spec!r}")
    try:
        return ZoneInfo(s)
    except (ZoneInfoNotFoundError, ValueError, OSError) as exc:
        raise ValueError(f"unknown timezone {spec!r}") from exc


def offset_spec(tz: tzinfo) -> str:
    """Stable string form of an offset (``UTC`` / ``+08:00``).

    The offset is read at a concrete instant: variable-offset zones
    (IANA names with DST) return ``None`` for ``utcoffset(None)``.
    """
    probe = datetime(2026, 1, 1, 12, tzinfo=tz)
    off = probe.utcoffset() or timedelta(0)
    if off == timedelta(0):
        return "UTC"
    total = int(off.total_seconds()) // 60
    sign = "+" if total >= 0 else "-"
    total = abs(total)
    return f"{sign}{total // 60:02d}:{total % 60:02d}"


def parse_dt(value: str | datetime, tz: str | tzinfo | None = None) -> datetime:
    """Parse an ISO timestamp into the project timezone.

    Naive values are interpreted as local wall time in ``tz``; aware
    values name their own instant and are converted.  Defaults to UTC.
    """
    zone = resolve_timezone(tz)
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=zone)
    return dt.astimezone(zone)


def to_iso(dt: datetime, tz: str | tzinfo | None = None) -> str:
    return parse_dt(dt, tz).isoformat()


def _is_workday(dt: datetime) -> bool:
    return dt.weekday() < 5


def _fraction(dt: datetime) -> float:
    """Elapsed fraction of the working window for a weekday moment."""
    seconds = (dt.hour * 3600 + dt.minute * 60 + dt.second
               + dt.microsecond / 1_000_000
               - WORK_START_HOUR * 3600)
    return min(1.0, max(0.0, seconds / _WORK_SECONDS))


def snap_forward(dt: datetime, tz: str | tzinfo | None = None) -> datetime:
    """Snap a moment forward into the next valid working instant.

    18:00 sharp is the (valid) end of the working window; only moments
    strictly after it roll to the next morning.
    """
    dt = parse_dt(dt, tz)
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


def forward_workdays(a: datetime, b: datetime,
                     tz: str | tzinfo | None = None) -> float:
    """Work capacity between two moments (``b >= a``), snapped to window."""
    a, b = snap_forward(a, tz), snap_forward(b, tz)
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


def work_offset(base: datetime, at: datetime,
                tz: str | tzinfo | None = None) -> float:
    """Signed workdays from ``base`` to ``at`` (negative when ``at`` earlier)."""
    base, at = parse_dt(base, tz), parse_dt(at, tz)
    if at >= base:
        return forward_workdays(base, at, tz)
    return -forward_workdays(at, base, tz)


def work_add(base: datetime, days: float,
             tz: str | tzinfo | None = None) -> datetime:
    """Move ``days`` workdays forward from ``base`` (days >= 0)."""
    cur = snap_forward(base, tz)
    remaining = float(days)
    # first (possibly partial) day: fill from the current window fraction
    room = 1.0 - _fraction(cur)
    if remaining <= room + 1e-12:
        return cur + timedelta(seconds=remaining * _WORK_SECONDS)
    remaining -= room
    cur = snap_forward(cur.replace(hour=WORK_START_HOUR, minute=0, second=0,
                                   microsecond=0) + timedelta(days=1), tz)
    while remaining > 1.0 + 1e-12:
        remaining -= 1.0
        cur = snap_forward(cur.replace(hour=WORK_START_HOUR, minute=0,
                                       second=0, microsecond=0)
                           + timedelta(days=1), tz)
    return cur + timedelta(seconds=remaining * _WORK_SECONDS)
