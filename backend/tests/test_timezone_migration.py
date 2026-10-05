"""API-level timezone behavior and legacy-data migration tests.

Covers:
* creating a +08:00 project and reporting against it over HTTP;
* explicit / invalid timezone fields;
* old rows in the database (written before the timezone field existed)
  being fixed automatically on startup, without re-entry;
* pure-Z legacy data producing byte-for-byte the same numbers;
* state/fever surviving a restart after migration.
"""
import importlib
import json
import os
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from conftest import BEIJING_REPORTS, beijing_payload


def _report(rid, task, at, kind="done", remaining=None):
    body = {"report_id": rid, "task_id": task, "at": at, "kind": kind}
    if remaining is not None:
        body["remaining_days"] = remaining
    return body


@pytest.fixture
def client(tmp_path, monkeypatch):
    db = tmp_path / "test.db"
    monkeypatch.setenv("CCPM_DB_PATH", str(db))
    monkeypatch.setenv("CCPM_STATIC_DIR", str(tmp_path / "nonexistent"))
    import app.main as main
    importlib.reload(main)
    with TestClient(main.app) as c:
        c.db_path = str(db)
        yield c


# ------------------------------ HTTP behavior ---------------------------- #

def test_create_offset_project_plans_in_local_window(client):
    resp = client.post("/api/projects",
                       json=beijing_payload("2026-01-05T09:00:00+08:00"))
    assert resp.status_code == 201, resp.text
    plan = resp.json()["plan"]
    assert plan["timezone"] == "Etc/GMT-8"
    assert plan["schedule"]["A"]["start_at"] == "2026-01-05T09:00:00+08:00"
    assert plan["schedule"]["A"]["finish_at"] == "2026-01-08T18:00:00+08:00"


def test_reports_offset_match_z_control_over_http(client):
    pid = client.post(
        "/api/projects",
        json=beijing_payload("2026-01-05T09:00:00+08:00")).json()["id"]
    for rep in BEIJING_REPORTS:
        r = client.post(f"/api/projects/{pid}/reports", json=rep)
        assert r.status_code == 200, r.text
    fever = client.get(f"/api/projects/{pid}/fever").json()
    assert [p["buffer_consumed_pct"] for p in fever["points"]] == \
        [0.0, 31.746032, 36.507937, 25.396825]
    # all four points stay below the 50% yellow line -> green
    assert [p["zone"] for p in fever["points"]] == \
        ["green", "green", "green", "green"]


def test_explicit_timezone_accepted_and_invalid_rejected(client):
    ok = client.post("/api/projects", json=beijing_payload(
        "2026-01-05T09:00:00", tz="Asia/Shanghai"))
    assert ok.status_code == 201, ok.text
    assert ok.json()["plan"]["timezone"] == "Asia/Shanghai"

    bad = client.post("/api/projects", json=beijing_payload(
        "2026-01-05T09:00:00", tz="Mars/Olympus"))
    assert bad.status_code == 422
    assert bad.json()["errors"][0]["field"] == "timezone"


def test_naive_report_at_is_read_in_project_zone(client):
    pid = client.post(
        "/api/projects",
        json=beijing_payload("2026-01-05T09:00:00+08:00")).json()["id"]
    r = client.post(f"/api/projects/{pid}/reports", json=_report(
        "r4", "A", "2026-01-09T17:00:00"))  # naive = 17:00 Beijing
    assert r.status_code == 200, r.text
    state = r.json()["state"]
    assert state["project_buffer"]["consumed_pct"] == 25.396825


# --------------------------- legacy migration ---------------------------- #

def _legacy_rows(start, reports, tz_on_payload=False):
    """Build (payload, plan) exactly as the pre-fix release stored them.

    The old code converted every aware stamp to UTC and ran the UTC calendar,
    so a +08:00 Monday 09:00 start was persisted as 01:00Z with task starts
    in the middle of the Beijing night.  No ``timezone`` key existed.
    """
    from app.scheduler import build_plan
    from app.validation import validate_project
    from app.worktime import parse_dt, work_add

    validated = validate_project(beijing_payload(start))
    utc_instant = parse_dt(start).isoformat()
    utc_plan = build_plan({**validated, "timezone": "UTC",
                           "start_time": utc_instant})
    payload = {k: validated[k] for k in
               ("name", "start_time", "tasks", "task_order",
                "resources", "thresholds")}
    base = parse_dt(start)
    for row in utc_plan["schedule"].values():
        row["start_at"] = work_add(base, row["start_day"]).isoformat()
        row["finish_at"] = work_add(base, row["finish_day"]).isoformat()
    utc_plan["project_start"] = base.isoformat()
    if tz_on_payload:
        payload["timezone"] = validated["timezone"]
    return payload, utc_plan, reports


def test_legacy_plus_eight_project_fixed_on_startup(tmp_path, monkeypatch):
    db = tmp_path / "leg.db"
    monkeypatch.setenv("CCPM_DB_PATH", str(db))
    monkeypatch.setenv("CCPM_STATIC_DIR", str(tmp_path / "nonexistent"))

    # Seed the file directly; Store construction itself does not migrate.
    from app.store import Store
    st = Store(str(db))
    payload, plan, reports = _legacy_rows(
        "2026-01-05T09:00:00+08:00", BEIJING_REPORTS)
    now = datetime.now(timezone.utc).isoformat()
    st.create_project("leg", "bj-demo", payload, plan, now)
    for rep in reports:
        st.add_report("leg", rep, now)
    # the old stored plan really did have the night-shift boundary
    assert plan["schedule"]["A"]["finish_at"].endswith("+00:00")

    # first process boot performs the migration
    import app.main as main
    importlib.reload(main)
    with TestClient(main.app) as c:
        plan_resp = c.get("/api/projects/leg/plan").json()
        new_plan = plan_resp["plan"]
        assert plan_resp["payload"]["timezone"] == "Etc/GMT-8"
        assert new_plan["timezone"] == "Etc/GMT-8"
        assert new_plan["schedule"]["A"]["start_at"] == \
            "2026-01-05T09:00:00+08:00"
        assert new_plan["schedule"]["A"]["finish_at"] == \
            "2026-01-08T18:00:00+08:00"
        fever = c.get("/api/projects/leg/fever").json()
        assert [p["buffer_consumed_pct"] for p in fever["points"]] == \
            [0.0, 31.746032, 36.507937, 25.396825]
        state = c.get("/api/projects/leg/state").json()
        assert state["project_buffer"]["consumed_pct"] == 25.396825

        # second boot: migration is idempotent and state is identical
        importlib.reload(main)
        with TestClient(main.app) as c2:
            state2 = c2.get("/api/projects/leg/state").json()
            fever2 = c2.get("/api/projects/leg/fever").json()
    assert state2 == state
    assert fever2 == fever


def test_legacy_pure_z_project_numbers_never_change(tmp_path, monkeypatch):
    db = tmp_path / "legz.db"
    monkeypatch.setenv("CCPM_DB_PATH", str(db))
    monkeypatch.setenv("CCPM_STATIC_DIR", str(tmp_path / "nonexistent"))

    z_reports = [{**r, "at": r["at"].replace("+08:00", "+00:00")}
                 for r in BEIJING_REPORTS]
    from app.store import Store
    st = Store(str(db))
    payload, plan, reports = _legacy_rows(
        "2026-01-05T09:00:00Z", z_reports)
    plan_json_before = json.dumps(plan, sort_keys=True)
    now = datetime.now(timezone.utc).isoformat()
    st.create_project("legz", "z-demo", payload, plan, now)
    for rep in reports:
        st.add_report("legz", rep, now)

    import app.main as main
    importlib.reload(main)
    with TestClient(main.app) as c:
        got = c.get("/api/projects/legz/plan").json()
        fever_after = c.get("/api/projects/legz/fever").json()

    assert got["payload"]["timezone"] == "UTC"
    assert got["plan"]["timezone"] == "UTC"
    # only the timezone key may appear; every prior field keeps its value
    before = json.loads(plan_json_before)
    for key, value in before.items():
        assert got["plan"][key] == value
    assert [p["buffer_consumed_pct"]
            for p in fever_after["points"]] == \
        [0.0, 31.746032, 36.507937, 25.396825]
