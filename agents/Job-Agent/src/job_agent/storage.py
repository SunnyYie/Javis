from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path

from .models import AgentError, JobPreferences, now


def encode(value) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False)


class Storage:
    def __init__(self, root: Path):
        self.root = root.expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = self.root / "job-agent.sqlite3"
        with self.connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY, created_at TEXT NOT NULL, state TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    session_id TEXT REFERENCES sessions(id) ON DELETE CASCADE,
                    id TEXT, payload TEXT NOT NULL,
                    PRIMARY KEY(session_id,id)
                );
                CREATE TABLE IF NOT EXISTS runs (
                    session_id TEXT REFERENCES sessions(id) ON DELETE CASCADE,
                    id TEXT, payload TEXT NOT NULL,
                    PRIMARY KEY(session_id,id)
                );
                CREATE TABLE IF NOT EXISTS job_versions (
                    session_id TEXT REFERENCES sessions(id) ON DELETE CASCADE,
                    run_id TEXT, id TEXT, payload TEXT NOT NULL,
                    PRIMARY KEY(session_id,run_id,id)
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY,
                    session_id TEXT REFERENCES sessions(id) ON DELETE CASCADE,
                    role TEXT NOT NULL, content TEXT NOT NULL
                );
            """)
        os.chmod(self.db, 0o600)

    @contextmanager
    def connect(self):
        with sqlite3.connect(self.db, timeout=10) as conn:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA secure_delete=ON")
            yield conn

    def session(self, sid: str | None = None) -> str:
        sid = sid or uuid.uuid4().hex[:16]
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", sid):
            raise AgentError("invalid_session", "会话 ID 只能包含字母、数字、下划线与连字符")
        with self.connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO sessions VALUES (?,?,?)",
                (sid, now(), encode({
                    "preferences": JobPreferences().model_dump(),
                    "resume": None, "confirmed": None, "remote_allowed": False,
                    "results": [], "result_context": None,
                })),
            )
        self.directory(sid)
        return sid

    def directory(self, sid: str) -> Path:
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", sid):
            raise AgentError("invalid_session")
        path = self.root / "sessions" / sid
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        return path

    def state(self, sid: str) -> dict:
        with self.connect() as conn:
            row = conn.execute("SELECT state FROM sessions WHERE id=?", (sid,)).fetchone()
        if not row:
            raise AgentError("unknown_session")
        return json.loads(row[0])

    def save_state(self, sid: str, state: dict):
        with self.connect() as conn:
            conn.execute("UPDATE sessions SET state=? WHERE id=?", (encode(state), sid))

    def put(self, table: str, sid: str, item, run_id: str | None = None):
        if table not in ("jobs", "runs"):
            raise ValueError("Invalid table")
        with self.connect() as conn:
            conn.execute(
                f"INSERT OR REPLACE INTO {table} VALUES (?,?,?)",
                (sid, item.id, encode(item)),
            )
            if table == "jobs" and run_id:
                conn.execute(
                    "INSERT OR REPLACE INTO job_versions VALUES (?,?,?,?)",
                    (sid, run_id, item.id, encode(item)),
                )

    def run_jobs(self, sid: str, run_id: str) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT payload FROM job_versions WHERE session_id=? AND run_id=? ORDER BY rowid",
                (sid, run_id),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def list(self, table: str, sid: str) -> list[dict]:
        if table not in ("jobs", "runs"):
            raise ValueError("Invalid table")
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT payload FROM {table} WHERE session_id=? ORDER BY rowid", (sid,)
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def message(self, sid: str, role: str, content: str):
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO messages(session_id,role,content) VALUES (?,?,?)",
                (sid, role, content),
            )

    def history(self, sid: str) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT role,content FROM messages WHERE session_id=? ORDER BY id", (sid,)
            ).fetchall()
        return [{"role": role, "content": content} for role, content in rows]

    def sessions(self) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute("SELECT id,created_at FROM sessions ORDER BY created_at").fetchall()
        return [{"id": sid, "created_at": created} for sid, created in rows]

    def delete(self, sid: str):
        self.state(sid)
        path = self.directory(sid)
        with self.connect() as conn:
            conn.execute("DELETE FROM sessions WHERE id=?", (sid,))
        shutil.rmtree(path)
