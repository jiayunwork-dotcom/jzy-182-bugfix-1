"""SQLite persistence for projects and the append-only report stream.

Two tables, both keyed on natural ids.  The report stream is *append only*:
``INSERT OR IGNORE`` keyed on (project_id, report_id) makes repeated
submission of the same report idempotent.  Ordering is enforced in the
projection layer by (at, report_id), so rows may be stored in arrival
order -- including out-of-order arrivals -- and replay still produces the
same state.

Recovery on restart requires no rebuild: tables are the source of truth;
project payloads/plan JSON are simply re-read.  WAL + a single writer lock
keep concurrent writers safe across processes sharing the file.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading


SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    plan_json    TEXT NOT NULL,
    created_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reports (
    project_id TEXT NOT NULL REFERENCES projects(id),
    report_id  TEXT NOT NULL,
    task_id    TEXT NOT NULL,
    at         TEXT NOT NULL,
    kind       TEXT NOT NULL,
    remaining_days REAL,
    created_at TEXT NOT NULL,
    seq        INTEGER NOT NULL,
    PRIMARY KEY (project_id, report_id)
);
CREATE INDEX IF NOT EXISTS reports_project_idx ON reports(project_id, seq);
"""


class Store:
    def __init__(self, path: str | None = None):
        path = path or os.environ.get("CCPM_DB_PATH", "/data/ccpm.db")
        dirpath = os.path.dirname(path)
        if dirpath:
            os.makedirs(dirpath, exist_ok=True)
        self.path = path
        self._writer_lock = threading.Lock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    # --------------------------- projects ------------------------------ #
    def create_project(self, pid: str, name: str, payload: dict,
                       plan: dict, created_at: str) -> None:
        with self._writer_lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO projects (id, name, payload_json, plan_json, "
                "created_at) VALUES (?, ?, ?, ?, ?)",
                (pid, name, json.dumps(payload, ensure_ascii=False),
                 json.dumps(plan, ensure_ascii=False), created_at))

    def list_projects(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, name, created_at FROM projects "
                "ORDER BY created_at, id").fetchall()
            return [dict(r) for r in rows]

    def get_project(self, pid: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["payload"] = json.loads(d.pop("payload_json"))
        d["plan"] = json.loads(d.pop("plan_json"))
        return d

    def iter_projects_raw(self):
        """Yield raw stored rows (payload/plan still JSON-encoded)."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, name, payload_json, plan_json, created_at "
                "FROM projects ORDER BY created_at, id").fetchall()
        yield from rows

    def update_project_json(self, pid: str, payload_json: str,
                            plan_json: str) -> None:
        """Replace stored payload/plan JSON (used by the timezone migration)."""
        with self._writer_lock, self._connect() as conn:
            conn.execute(
                "UPDATE projects SET payload_json=?, plan_json=? WHERE id=?",
                (payload_json, plan_json, pid))

    # --------------------------- reports ------------------------------- #
    def add_report(self, project_id: str, report: dict, created_at: str):
        """Append a report. Returns True if inserted, False if duplicate."""
        with self._writer_lock, self._connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) AS m FROM reports "
                "WHERE project_id=?", (project_id,)).fetchone()
            next_seq = row["m"] + 1
            cur = conn.execute(
                "INSERT OR IGNORE INTO reports (project_id, report_id, "
                "task_id, at, kind, remaining_days, created_at, seq) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (project_id, report["report_id"], report["task_id"],
                 report["at"], report["kind"], report.get("remaining_days"),
                 created_at, next_seq))
            return cur.rowcount == 1

    def list_reports(self, project_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT report_id, task_id, at, kind, remaining_days "
                "FROM reports WHERE project_id=? ORDER BY seq",
                (project_id,)).fetchall()
            return [dict(r) for r in rows]
