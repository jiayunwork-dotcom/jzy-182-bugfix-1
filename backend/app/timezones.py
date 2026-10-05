"""Project time-zone resolution.

Every project follows one working calendar whose 09:00-18:00 Mon-Fri window
is read on a specific zone's wall clock.  This module decides *which* zone:

* ``timezone`` given explicitly on the project  -> an IANA name such as
  ``Asia/Shanghai`` (recommended), or a fixed offset spec ``UTC+HH:MM`` /
  ``Etc/GMT-8``.
* omitted on a **new** project                   -> derived from the
  UTC offset of ``start_time`` (a ``+08:00`` start therefore follows a
  +08:00 calendar with no extra configuration; a ``Z`` start stays UTC).
* absent on an **old** project (created before this field existed) ->
  inferred from the offsets seen on ``start_time`` and the stored reports,
  see :func:`infer_timezone_name`.

Naive timestamps (no offset at all) are always interpreted in the project's
own zone.  Rationale and costs are documented in docs/DECISIONS.md.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone, tzinfo

from zoneinfo import ZoneInfo
from zoneinfo import ZoneInfoNotFoundError

UTC = timezone.utc

_OFFSET_RE = re.compile(
    r"^(Etc/GMT|UTC|GMT)([+-])(\d{1,2})(?::?([0-5]\d))?$")


class UnknownTimezone(ValueError):
    """Raised when a timezone field cannot be resolved."""


def get_tz(name: str):
    """Resolve one stored timezone name (IANA or UTC/GMT fixed offset)."""
    if not isinstance(name, str) or not name.strip():
        raise UnknownTimezone("timezone name must be a non-empty string")
    text = name.strip()
    m = _OFFSET_RE.match(text)
    if m:
        prefix, sign, hours, minutes = m.groups()
        delta = timedelta(hours=int(hours), minutes=int(minutes or 0))
        # Etc/GMT uses the POSIX convention with an inverted sign:
        # Etc/GMT-8 == UTC+8, Etc/GMT+5 == UTC-5.
        if prefix == "Etc/GMT":
            sign = "-" if sign == "+" else "+"
        if sign == "-":
            delta = -delta
        if timedelta(hours=-24) < delta < timedelta(hours=24):
            return timezone(delta, name=text)
        raise UnknownTimezone(f"fixed offset out of range: {name!r}")
    try:
        return ZoneInfo(text)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise UnknownTimezone(f"unknown timezone {name!r}") from exc


def fixed_offset_name(offset_minutes: int) -> str:
    """Canonical, ZoneInfo-resolvable name for a fixed UTC offset."""
    if offset_minutes == 0:
        return "UTC"
    sign = "+" if offset_minutes > 0 else "-"
    total = abs(offset_minutes)
    hh, mm = divmod(total, 60)
    if mm == 0:
        # POSIX Etc zones carry the *inverted* sign: Etc/GMT-8 == UTC+8.
        return f"Etc/GMT{'+' if offset_minutes < 0 else '-'}{hh}"
    return f"UTC{sign}{hh:02d}:{mm:02d}"


def _offset_minutes(value: str | datetime) -> int | None:
    """UTC offset of a timestamp in whole minutes; None when naive."""
    dt = value if isinstance(value, datetime) else datetime.fromisoformat(
        value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        return None
    return int(dt.utcoffset().total_seconds() // 60)


def derive_for_start(start_time: str | datetime | None) -> tuple[str, tzinfo]:
    """Default zone for a project with no explicit timezone.

    Uses the start timestamp's own UTC offset (naive -> UTC), so a project
    that opens at ``...+08:00`` works on a +08:00 calendar automatically.
    """
    if start_time is None:
        return "UTC", UTC
    minutes = _offset_minutes(start_time)
    if minutes is None or minutes == 0:
        return "UTC", UTC
    name = fixed_offset_name(minutes)
    return name, get_tz(name)


def infer_timezone_name(start_time: str | None,
                        report_ats: list[str]) -> str:
    """Infer the work-zone of a legacy project without a timezone field.

    Offsets are collected from the project start and every stored report.

    * no non-zero offset anywhere           -> ``UTC`` (pure-Z projects stay
      byte-for-byte on their old numbers);
    * exactly one distinct non-zero offset  -> a fixed-offset zone with that
      offset (the Beijing case: every stamp is +08:00);
    * several distinct non-zero offsets     -> the start's offset wins if it
      is non-zero, otherwise the most frequent non-zero offset, ties broken
      by the smallest offset.  Mixed-offset legacy data is not expected;
      the rule only guarantees a deterministic, auditable answer.
    Naive stamps never vote (they were assumed UTC before and keep doing so
    unless another stamp establishes the zone).
    """
    votes: list[int] = []
    if start_time is not None:
        off = _offset_minutes(start_time)
        if off:
            votes.append(off)
    for at in report_ats:
        off = _offset_minutes(at)
        if off:
            votes.append(off)

    nonzero = [v for v in votes if v != 0]
    if not nonzero:
        return "UTC"
    distinct = set(nonzero)
    if len(distinct) == 1:
        return fixed_offset_name(nonzero[0])

    start_off = _offset_minutes(start_time) if start_time is not None else None
    if start_off:
        return fixed_offset_name(start_off)
    counts = {off: nonzero.count(off) for off in distinct}
    winner = min(distinct, key=lambda off: (-counts[off], off))
    return fixed_offset_name(winner)


