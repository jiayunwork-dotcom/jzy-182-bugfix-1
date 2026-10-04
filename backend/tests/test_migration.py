"""Legacy-data migration tests (project timezone introduction).

Projects created before timezones existed have no ``timezone`` field and a
plan built on the UTC calendar.  On startup the store is migrated in place:
the working timezone is inferred from the offsets actually used by the
project's timestamps, the plan is rebuilt, and reports stay byte-identical.

Invariants:

* an all-``+08:00`` project comes out exactly as if it had been created
  with ``timezone: "+08:00"`` (fever points match the Z control group);
* an all-``Z`` project is left *completely* untouched (bytes and numbers);
* migration is idempotent across restarts;
* history replay and out-of-order/concurrency folding keep working.
"""
import threading

import pytest
from fastapi.testclient import TestClient


REPORT_WALLS = [
    ("r1", "2026-01-05T09:00:00", "remaining", 4),
    ("r2", "2026-01-09T10:00:00", "remaining", 1),
    ("r3", "2026-01-09T16:00:00", "remaining", 0.5),
    ("r4", "2026-01-09T17:00:00", "done", None),
]
EXPECTED_PCT = [0.0, 31.746032, 36.507937, 25.396825]


def _report(rid, task, at, kind="done", remaining=None):
    body = {"report_id": rid, "task_id": task, "at": at, "kind": kind}
    if remaining is not None:
        body["remaining_days"] = remaining
    return body


def _payload(start_time):
    return {
        "name": "demo",
        "start_time": start_time,
        "resources": [{"id": "eng", "name": "engineer", "capacity": 1}],
        "tasks": [
            {"id": "A", "name": "Spec", "predecessors": [],
             "demands": {"eng": 1}, "aggressive_days": 4,
             "conservative_days": 5},
            {"id": "B", "name": "Build-backend", "predecessors": ["A"],
             "demands": {"eng": 1}, "aggressive_days": 6,
             "conservative_days": 9},
            {"id": "C", "name": "Build-frontend", "predecessors": ["A"],
             "demands": {"eng": 1}, "aggressive_days": 3,
             "conservative_days": 5},
            {"id": "D", "name": "Release", "predecessors": ["B", "C"],
             "demands": {}, "aggressive_days": 2,
             "conservative_days": 3},
        ],
        "thresholds": {"yellow_pct": 50, "red_pct": 80},
    }


def _legacy_project(store, pid, start_suffix):
    """Insert a project exactly as the old release stored it."""
    from app.scheduler import build_plan
    from app.validation import validate_project
    v = validate_project(_payload(f"2026-01-05T09:00:00{start_suffix}"))
    # old release: no timezone concept anywhere
    v_old = {k: vv for k, vv in v.items() if k != "timezone"}
    plan = build_plan({**v_old, "timezone": "UTC"})
    plan.pop("timezone", None)
    normalized = {
        "name": v["name"], "start_time": v["start_time"],
        "tasks": v["tasks"], "task_order": v["task_order"],
        "resources": v["resources"], "thresholds": v["thresholds"],
    }
    store.create_project(pid, v["name"], normalized, plan,
                         "2026-01-05T01:00:00+00:00")
    for rid, wall, kind, rem in REPORT_WALLS:
        store.add_report(
            pid, _report(rid, "A", wall + start_suffix, kind, rem),
            "2026-01-09T09:00:00+00:00")


@pytest.fixture
def legacy_db(tmp_path):
    db = tmp_path / "legacy.db"
    from app.store import Store
    store = Store(str(db))
    _legacy_project(store, "pid-bj", "+08:00")
    _legacy_project(store, "pid-z", "Z")
    raw_z_before = next(
        r for r in store.iter_projects_raw() if r["id"] == "pid-z")
    del store
    return str(db), raw_z_before


def _boot(db, tmp_path):
    import importlib
    import os
    os.environ["CCPM_DB_PATH"] = db
    os.environ["CCPM_STATIC_DIR"] = str(tmp_path / "nonexistent")
    import app.main as main
    importlib.reload(main)
    return TestClient(main.app), main.app.state.store


def test_migration_repairs_bj_project_and_preserves_z_project(
        tmp_path, legacy_db):
    db, raw_z_before = legacy_db
    client, store = _boot(db, tmp_path)
    with client:
        # Beijing project: migrated to fixed +08:00
        p = store.get_project("pid-bj")
        assert p["payload"]["timezone"] == "+08:00"
        assert p["payload"].get("_migrated_timezone") is True
        assert p["plan"]["timezone"] == "+08:00"
        # planned moments land in Beijing working hours
        assert p["plan"]["schedule"]["A"]["start_at"] == \
            "2026-01-05T09:00:00+08:00"
        assert p["plan"]["schedule"]["A"]["finish_at"] == \
            "2026-01-08T18:00:00+08:00"
        fever = client.get("/api/projects/pid-bj/fever").json()
        assert [pt["buffer_consumed_pct"] for pt in fever["points"]] == \
            EXPECTED_PCT
        state = client.get("/api/projects/pid-bj/state").json()
        assert state["project_buffer"]["consumed_pct"] == EXPECTED_PCT[-1]

        # Z project: stored bytes untouched, every number unchanged
        raw_z_after = next(
            r for r in store.iter_projects_raw() if r["id"] == "pid-z")
        assert raw_z_after["payload_json"] == raw_z_before["payload_json"]
        assert raw_z_after["plan_json"] == raw_z_before["plan_json"]
        pz = store.get_project("pid-z")
        assert "timezone" not in pz["payload"]
        zfever = client.get("/api/projects/pid-z/fever").json()
        assert [pt["buffer_consumed_pct"] for pt in zfever["points"]] == \
            EXPECTED_PCT


def test_migration_idempotent_across_restart(tmp_path, legacy_db):
    db, _ = legacy_db
    client1, store1 = _boot(db, tmp_path)
    with client1:
        s1 = client1.get("/api/projects/pid-bj/state").json()
        f1 = client1.get("/api/projects/pid-bj/fever").json()
    # restart (fresh module state, same file): migration must be a no-op
    client2, store2 = _boot(db, tmp_path)
    with client2:
        s2 = client2.get("/api/projects/pid-bj/state").json()
        f2 = client2.get("/api/projects/pid-bj/fever").json()
        p = store2.get_project("pid-bj")
        assert p["payload"]["timezone"] == "+08:00"
    assert s2 == s1
    assert f2 == f1


def test_history_replay_after_migration(tmp_path, legacy_db):
    db, _ = legacy_db
    client, _store = _boot(db, tmp_path)
    with client:
        fever = client.get("/api/projects/pid-bj/fever").json()
        for point in fever["points"]:
            replayed = client.get(
                "/api/projects/pid-bj/state",
                params={"at": point["at"]}).json()
            assert replayed["at"] == point["at"]
            assert replayed["project_buffer"]["consumed_pct"] == \
                point["buffer_consumed_pct"]
            assert replayed["tasks"] == point["tasks"]


def test_inferred_offset_unit():
    """Unit-level checks of the majority-vote inference, incl. the tie."""
    from app.migration import infer_timezone

    def pl(start="2026-01-05T09:00:00+08:00"):
        return {"name": "x", "start_time": start}

    assert infer_timezone(pl(), [
        {"at": "2026-01-09T17:00:00+08:00"}])[0] == "+08:00"
    assert infer_timezone(pl("2026-01-05T09:00:00Z"), [
        {"at": "2026-01-09T17:00:00Z"}])[0] == "UTC"
    # naive timestamps carry no signal -> UTC
    assert infer_timezone(pl("2026-01-05T09:00:00"), [
        {"at": "2026-01-09T17:00:00"}])[0] == "UTC"
    # tie -> UTC, flagged
    tz, ambiguous = infer_timezone(
        pl("2026-01-05T09:00:00Z"),
        [{"at": "2026-01-09T17:00:00+08:00"}])
    assert tz == "UTC"
    assert ambiguous is True
    # majority wins even with a minority of foreign offsets
    assert infer_timezone(pl(), [
        {"at": "2026-01-09T17:00:00+08:00"},
        {"at": "2026-01-10T09:00:00+08:00"},
        {"at": "2026-01-11T09:00:00Z"}])[0] == "+08:00"


def test_naive_timestamps_treated_as_project_timezone_via_http(
        tmp_path, monkeypatch):
    db = tmp_path / "naive.db"
    monkeypatch.setenv("CCPM_DB_PATH", str(db))
    monkeypatch.setenv("CCPM_STATIC_DIR", str(tmp_path / "nonexistent"))
    import importlib
    import app.main as main
    importlib.reload(main)
    with TestClient(main.app) as c:
        payload = _payload("2026-01-05T09:00:00")  # no offset
        payload["timezone"] = "Asia/Shanghai"
        pid = c.post("/api/projects", json=payload).json()["id"]
        plan = c.get(f"/api/projects/{pid}/plan").json()["plan"]
        assert plan["schedule"]["A"]["start_at"] == \
            "2026-01-05T09:00:00+08:00"
        for rid, wall, kind, rem in REPORT_WALLS:
            body = _report(rid, "A", wall, kind, rem)  # naive at
            r = c.post(f"/api/projects/{pid}/reports", json=body)
            assert r.status_code == 200, r.text
        fever = c.get(f"/api/projects/{pid}/fever").json()
        assert [pt["buffer_consumed_pct"] for pt in fever["points"]] == \
            EXPECTED_PCT


def test_invalid_timezone_rejected(tmp_path, monkeypatch):
    db = tmp_path / "bad.db"
    monkeypatch.setenv("CCPM_DB_PATH", str(db))
    monkeypatch.setenv("CCPM_STATIC_DIR", str(tmp_path / "nonexistent"))
    import importlib
    import app.main as main
    importlib.reload(main)
    with TestClient(main.app) as c:
        payload = _payload("2026-01-05T09:00:00")
        payload["timezone"] = "Asia/NoSuchCity"
        r = c.post("/api/projects", json=payload)
        assert r.status_code == 422
        assert r.json()["errors"][0]["field"] == "timezone"


def test_malformed_report_timestamp_rejected(tmp_path, monkeypatch):
    db = tmp_path / "badts.db"
    monkeypatch.setenv("CCPM_DB_PATH", str(db))
    monkeypatch.setenv("CCPM_STATIC_DIR", str(tmp_path / "nonexistent"))
    import importlib
    import app.main as main
    importlib.reload(main)
    with TestClient(main.app) as c:
        payload = _payload("2026-01-05T09:00:00")
        payload["timezone"] = "Asia/Shanghai"
        pid = c.post("/api/projects", json=payload).json()["id"]
        r = c.post(f"/api/projects/{pid}/reports",
                   json=_report("x", "A", "not-a-timestamp"))
        assert r.status_code == 422
        assert r.json()["errors"][0]["field"] == "at"


def test_concurrent_reports_after_migration_match_serial(
        tmp_path, legacy_db):
    db, _ = legacy_db
    import importlib
    import os
    os.environ["CCPM_DB_PATH"] = db
    os.environ["CCPM_STATIC_DIR"] = str(tmp_path / "nonexistent")
    import app.main as main
    importlib.reload(main)  # triggers the migration once

    # extra reports on the migrated Beijing project, hammered concurrently
    extra = [
        _report("c1", "B", "2026-01-19T18:00:00+08:00"),
        _report("c2", "C", "2026-01-22T12:00:00+08:00",
                kind="remaining", remaining=1),
        _report("c3", "C", "2026-01-22T18:00:00+08:00"),
        _report("c4", "D", "2026-01-26T10:00:00+08:00",
                kind="remaining", remaining=1),
        _report("c4", "D", "2026-01-26T10:00:00+08:00",
                kind="remaining", remaining=1),
    ]
    errors = []

    def hammer(app, items):
        with TestClient(app) as local:
            for b in items:
                r = local.post("/api/projects/pid-bj/reports", json=b)
                if r.status_code != 200:
                    errors.append(r.status_code)

    # all threads share the one application/store, like a uvicorn process
    threads = [threading.Thread(target=hammer, args=(main.app, chunk))
               for chunk in (extra[0::2], extra[1::2])]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors

    with TestClient(main.app) as c:
        par = c.get("/api/projects/pid-bj/state").json()
    # serial reference: a freshly migrated twin project
    db2 = str(tmp_path / "legacy2.db")
    from app.store import Store
    store2 = Store(db2)
    _legacy_project(store2, "pid-bj", "+08:00")
    del store2
    os.environ["CCPM_DB_PATH"] = db2
    importlib.reload(main)
    with TestClient(main.app) as c:
        for b in extra:
            c.post("/api/projects/pid-bj/reports", json=b)
        ser = c.get("/api/projects/pid-bj/state").json()
    assert par == ser
