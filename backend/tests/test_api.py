"""End-to-end tests through the FastAPI app: API, persistence, recovery,
idempotency and concurrency.
"""
import os
import threading

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    db = tmp_path / "test.db"
    monkeypatch.setenv("CCPM_DB_PATH", str(db))
    monkeypatch.setenv("CCPM_STATIC_DIR", str(tmp_path / "nonexistent"))
    # import after env is set so Store picks up the path
    import importlib
    import app.main as main
    importlib.reload(main)
    with TestClient(main.app) as c:
        c.db_path = str(db)
        yield c


@pytest.fixture
def project_id(client):
    from conftest import four_task_payload
    resp = client.post("/api/projects", json=four_task_payload(1))
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_health(client):
    assert client.get("/api/health").json() == {"ok": True}


def test_create_project_plan_15(client, project_id):
    resp = client.get(f"/api/projects/{project_id}/plan")
    assert resp.status_code == 200
    plan = resp.json()["plan"]
    assert plan["makespan_days"] == 15
    assert plan["critical_chain"] == ["A", "B", "C", "D"]


def test_create_project_validation_error_names_field(client):
    resp = client.post("/api/projects", json={
        "name": "x",
        "resources": [{"id": "r", "name": "r", "capacity": 1}],
        "tasks": [{"id": "t", "name": "t", "predecessors": ["nope"],
                   "demands": {}, "aggressive_days": 1,
                   "conservative_days": 2}],
        "thresholds": {"yellow_pct": 50, "red_pct": 80}})
    assert resp.status_code == 422
    err = resp.json()["errors"][0]
    assert "predecessors" in err["field"]


def test_invalid_json_body(client):
    resp = client.post("/api/projects", content="{not json",
                       headers={"content-type": "application/json"})
    assert resp.status_code == 422


def _report(rid, task, at, kind="done", remaining=None):
    body = {"report_id": rid, "task_id": task, "at": at, "kind": kind}
    if remaining is not None:
        body["remaining_days"] = remaining
    return body


def test_report_idempotent(client, project_id):
    start = "2026-01-05T09:00:00Z"
    # A (4 workdays) finishes exactly on time Thursday Jan 8 18:00
    payload = _report("r1", "A", "2026-01-08T18:00:00Z")
    r1 = client.post(f"/api/projects/{project_id}/reports", json=payload)
    r2 = client.post(f"/api/projects/{project_id}/reports", json=payload)
    assert r1.json()["inserted"] is True
    assert r2.json()["inserted"] is False
    assert r2.json()["duplicate"] is True
    assert r1.json()["state"] == r2.json()["state"]


def test_report_unknown_task_rejected(client, project_id):
    resp = client.post(f"/api/projects/{project_id}/reports",
                       json=_report("x", "GHOST", "2026-01-08T18:00:00Z"))
    assert resp.status_code == 422
    assert "task_id" in resp.json()["errors"][0]["field"]


def test_out_of_order_via_http_matches_serial(client, project_id):
    plan = client.get(f"/api/projects/{project_id}/plan").json()["plan"]
    start = plan["project_start"]
    from app.worktime import work_add
    def d(n):
        return work_add(start, n).isoformat()

    ordered = [
        _report("r1", "A", d(4)),
        _report("r2", "B", d(10)),
        _report("r3", "C", d(14), kind="remaining", remaining=1),
    ]
    # insert in jumbled arrival order
    for body in [ordered[2], ordered[0], ordered[1]]:
        client.post(f"/api/projects/{project_id}/reports", json=body)
    state_jumbled = client.get(f"/api/projects/{project_id}/state").json()

    # fresh project, strictly serial in timestamp order
    pid2 = client.post(
        "/api/projects",
        json=client.get(f"/api/projects/{project_id}/plan").json()["payload"]
        if False else __import__("conftest").four_task_payload(1)).json()["id"]
    for body in ordered:
        client.post(f"/api/projects/{pid2}/reports", json=body)
    state_serial = client.get(f"/api/projects/{pid2}/state").json()

    assert state_jumbled == state_serial


def test_history_state_endpoint_matches_replay(client, project_id):
    from app.worktime import work_add
    plan = client.get(f"/api/projects/{project_id}/plan").json()["plan"]
    def d(n):
        return work_add(plan["project_start"], n).isoformat()
    times = [d(4), d(11), d(13), d(17)]
    for rid, task, t in [("r1", "A", times[0]), ("r2", "B", times[1]),
                         ("r3", "C", times[2]), ("r4", "D", times[3])]:
        client.post(f"/api/projects/{project_id}/reports",
                    json=_report(rid, task, t))
    fever = client.get(f"/api/projects/{project_id}/fever").json()
    assert len(fever["points"]) == 4
    for point in fever["points"]:
        replayed = client.get(
            f"/api/projects/{project_id}/state",
            params={"at": point["at"]}).json()
        assert replayed["at"] == point["at"]
        assert replayed["project_buffer"]["consumed_pct"] == \
            point["buffer_consumed_pct"]
        assert replayed["critical_chain_completion_pct"] == \
            point["critical_chain_completion_pct"]
        assert replayed["tasks"] == point["tasks"]


def test_restart_recovery(tmp_path, monkeypatch):
    db = tmp_path / "rec.db"
    monkeypatch.setenv("CCPM_DB_PATH", str(db))
    monkeypatch.setenv("CCPM_STATIC_DIR", str(tmp_path / "nonexistent"))
    import importlib
    import app.main as main

    def fresh_client():
        importlib.reload(main)
        return TestClient(main.app)

    with fresh_client() as c:
        pid = c.post("/api/projects",
                     json=__import__("conftest").four_task_payload(1)
                     ).json()["id"]
        c.post(f"/api/projects/{pid}/reports",
               json=_report("r1", "A", "2026-01-08T18:00:00Z"))
        before = c.get(f"/api/projects/{pid}/state").json()
        fever_before = c.get(f"/api/projects/{pid}/fever").json()

    # restart: new process = fresh module state, same SQLite file
    with fresh_client() as c2:
        after = c2.get(f"/api/projects/{pid}/state").json()
        fever_after = c2.get(f"/api/projects/{pid}/fever").json()
        projects = c2.get("/api/projects").json()["projects"]
    assert [p["id"] for p in projects] == [pid]
    assert after == before
    assert fever_after == fever_before


def test_concurrent_reports_match_serial(tmp_path, monkeypatch):
    db = tmp_path / "conc.db"
    monkeypatch.setenv("CCPM_DB_PATH", str(db))
    monkeypatch.setenv("CCPM_STATIC_DIR", str(tmp_path / "nonexistent"))
    import importlib
    import app.main as main

    def payload_factory():
        return __import__("conftest").four_task_payload(1)

    def build_app():
        importlib.reload(main)
        return main.app

    from app.worktime import work_add
    # two projects: one hammered concurrently, one fed serially
    app = build_app()
    with TestClient(app) as c:
        pid_par = c.post("/api/projects", json=payload_factory()).json()["id"]
        pid_ser = c.post("/api/projects", json=payload_factory()).json()["id"]
        plan = c.get(f"/api/projects/{pid_par}/plan").json()["plan"]
        def d(n):
            return work_add(plan["project_start"], n).isoformat()
        bodies = [
            _report("r1", "A", d(4)),
            _report("r2", "B", d(10)),
            _report("r3", "C", d(13)),
            _report("r4", "D", d(16)),
            _report("r4", "D", d(16)),  # duplicate to stress idempotency
            _report("r5", "A", d(5), kind="remaining", remaining=0),
        ]

    errors = []

    def hammer(pid, items):
        with TestClient(build_app()) as local:
            for b in items:
                r = local.post(f"/api/projects/{pid}/reports", json=b)
                if r.status_code != 200:
                    errors.append(r.status_code)

    threads = []
    # split bodies three ways, duplicated ids across threads on purpose
    chunks = [bodies[0::3], bodies[1::3], bodies[2::3]]
    for chunk in chunks:
        t = threading.Thread(target=hammer, args=(pid_par, chunk))
        threads.append(t)
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors

    with TestClient(build_app()) as c:
        # unique report ids are r1..r5 (r4 is submitted twice)
        for b in bodies:
            c.post(f"/api/projects/{pid_ser}/reports", json=b)
        final_par = c.get(f"/api/projects/{pid_par}/state").json()
        final_ser = c.get(f"/api/projects/{pid_ser}/state").json()
        assert final_par["report_count"] == final_ser["report_count"] == 5

    # Same (at, report_id) stream -> same projected state regardless of
    # interleaving; compare the buffer/completion signals.
    for key in ("critical_chain_completion_pct", "zone"):
        assert final_par[key] == final_ser[key]
    assert final_par["project_buffer"] == final_ser["project_buffer"]
    assert final_par["tasks"] == final_ser["tasks"]
