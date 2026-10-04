"""One-time migration of projects created before project timezones existed.

Old rows have no ``timezone`` field in their payload/plan.  The working
calendar used to be evaluated in UTC, which was wrong for teams recording
wall-clock moments outside UTC -- but their stored timestamps are not
rewritten.  Instead we *infer* the timezone the team works in and rebuild
the plan under it:

* every timestamp carrying an explicit offset (``Z`` / ``+08:00``) casts
  one vote for its offset;
* the majority offset wins.  In the common case every timestamp uses one
  offset (e.g. all ``+08:00``) so the result is unambiguous;
* an all-Z/UTC project resolves to UTC, which reproduces the old calendar
  exactly -- its numbers never change;
* a tie (e.g. half Z, half +08:00) falls back to UTC and is logged, since
  the data itself does not name a single working timezone;
* a project whose timestamps are all naive carries no signal either; it
  also stays UTC (identical to the old interpretation).

The inference deliberately uses **fixed offsets** (``+08:00``), not IANA
names: the data only ever reveals an offset, never a region's DST rules.
A fixed offset is exactly what the Beijing calendar needs (China has no
DST); teams in DST regions should set the IANA name explicitly on new
projects.

Migration is idempotent: projects that already have a timezone are
untouched, and reports are never modified.
"""
from __future__ import annotations

import json
import logging
import threading
from collections import Counter
from datetime import datetime, timedelta, timezone as _tz

from .scheduler import build_plan
from .worktime import offset_spec

log = logging.getLogger("ccpm.migration")

MIGRATION_MARKER = "_migrated_timezone"
_migration_lock = threading.Lock()


def _explicit_offset(value: str) -> str | None:
    """Offset spec (``UTC`` / ``+08:00``) of a timestamp, None if naive."""
    try:
        dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    return offset_spec(_tz(dt.utcoffset() or timedelta(0)))


def infer_timezone(payload: dict, reports: list[dict]) -> tuple[str, bool]:
    """Majority-vote the working timezone from explicit timestamp offsets.

    Returns ``(timezone, ambiguous)``.  ``ambiguous`` flags an offset tie;
    both "no signal" (all naive) and an explicit UTC majority resolve to
    UTC, under which the old numbers already hold exactly.
    """
    votes: Counter = Counter()
    start = payload.get("start_time")
    off = _explicit_offset(start) if isinstance(start, str) else None
    if off is not None:
        votes[off] += 1
    for rep in reports:
        off = _explicit_offset(rep.get("at"))
        if off is not None:
            votes[off] += 1
    if not votes:
        return "UTC", False
    top = votes.most_common()
    spec, n1 = top[0]
    if len(top) > 1 and top[1][1] == n1:
        log.warning("project %r has tied timezone votes %s; defaulting to UTC",
                    payload.get("name"), dict(votes))
        return "UTC", True
    return spec, False


def migrate_project_row(payload: dict, plan: dict,
                        reports: list[dict]) -> tuple[dict, dict, str] | None:
    """Return (new_payload, new_plan, timezone) if the row must change.

    Returns None for projects that are already on a timezone, and for
    legacy projects that resolve to UTC: their old plan was already
    computed under a UTC calendar, so leaving the bytes untouched keeps
    every number and every stored timestamp identical.
    """
    if isinstance(payload, dict) and payload.get("timezone"):
        return None
    if isinstance(plan, dict) and plan.get("timezone"):
        # Plan rebuilt by a newer version but payload lacks the field:
        # just mirror the plan's timezone into the payload.
        tz = plan["timezone"]
    else:
        tz, _ambiguous = infer_timezone(payload or {}, reports)
    if tz == "UTC":
        return None

    new_payload = dict(payload or {})
    new_payload["timezone"] = tz
    new_payload[MIGRATION_MARKER] = True

    # Rebuild the plan under the inferred timezone.  All workday-level
    # numbers (chain, days, buffers) come out identical; only the absolute
    # start_at/finish_at instants move onto the correct wall-clock grid.
    rebuild_input = {k: v for k, v in new_payload.items()
                     if k != MIGRATION_MARKER}
    new_plan = build_plan(rebuild_input)
    return new_payload, new_plan, tz


def migrate_store(store) -> int:
    """Migrate every legacy project in the store. Returns project count."""
    with _migration_lock:
        migrated = 0
        for row in store.iter_projects_raw():
            pid = row["id"]
            payload = json.loads(row["payload_json"])
            plan = json.loads(row["plan_json"])
            if payload.get("timezone"):
                continue
            reports = store.list_reports(pid)
            result = migrate_project_row(payload, plan, reports)
            if result is None:
                continue
            new_payload, new_plan, tz = result
            store.update_project_json(
                pid,
                json.dumps(new_payload, ensure_ascii=False),
                json.dumps(new_plan, ensure_ascii=False))
            log.warning("migrated project %s (%s) to timezone %s based on %d "
                        "report timestamp(s) plus the project start",
                        pid, payload.get("name"), tz, len(reports))
            migrated += 1
        return migrated
