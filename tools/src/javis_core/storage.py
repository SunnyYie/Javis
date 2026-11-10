import json
import os
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from filelock import FileLock

from .models import BrainError, digest, now

SCHEMA = [
    "CREATE TABLE team_sessions(id TEXT PRIMARY KEY, payload TEXT NOT NULL)",
    "CREATE TABLE team_tasks(id TEXT PRIMARY KEY, sid TEXT NOT NULL, payload TEXT NOT NULL)",
    "CREATE TABLE team_events(id INTEGER PRIMARY KEY, task_id TEXT, payload TEXT NOT NULL)",
]


def fingerprint(db):
    return digest(list(db.iterdump()))


def migrate(path):
    with FileLock(str(path) + ".migration.lock", timeout=30):
        _migrate(path)


def _migrate(path):
    """Additive, transactional migration; existing business tables are untouched."""
    path = Path(path)
    existed = path.exists()
    with sqlite3.connect(path) as db:
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version == 1:
            return
        if version != 0:
            raise BrainError("unsupported_schema")
        backup = path.with_suffix(path.suffix + ".v0.bak")
        if existed:
            if backup.exists():
                with sqlite3.connect(backup) as target:
                    if fingerprint(db) != fingerprint(target):
                        raise BrainError("migration_backup_mismatch")
            else:
                with sqlite3.connect(backup) as target:
                    db.backup(target)
            os.chmod(backup, 0o600)
        try:
            db.execute("BEGIN IMMEDIATE")
            for statement in SCHEMA:
                db.execute(statement)
            db.execute("PRAGMA user_version=1")
            if existed:
                manifest = path.with_suffix(path.suffix + ".migration.json")
                manifest.write_text(json.dumps({"after": fingerprint(db)}), encoding="utf-8")
                os.chmod(manifest, 0o600)
            db.commit()
        except BaseException:
            db.rollback()
            raise
    os.chmod(path, 0o600)


def rollback(path):
    path = Path(path)
    backup = path.with_suffix(path.suffix + ".v0.bak")
    manifest = path.with_suffix(path.suffix + ".migration.json")
    if not backup.is_file() or not manifest.is_file():
        raise BrainError("no_migration_backup")
    with sqlite3.connect(path) as db:
        db.execute("BEGIN EXCLUSIVE")
        if fingerprint(db) != json.loads(manifest.read_text())["after"]:
            raise BrainError("rollback_new_writes", "迁移后已有写入，拒绝覆盖")
        # Drop only the additive schema; preserve the original records and references.
        for name in ("team_events", "team_tasks", "team_sessions"):
            db.execute(f"DROP TABLE {name}")
        db.execute("PRAGMA user_version=0")
    os.chmod(path, 0o600)


class TaskStore:
    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.root / "tasks.sqlite3"
        migrate(self.path)

    @contextmanager
    def connect(self):
        with sqlite3.connect(self.path, timeout=30) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA secure_delete=ON")
            yield db

    def session(self, sid):
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", sid):
            raise BrainError("invalid_id")
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO team_sessions VALUES(?,?)", (sid, "{}"))
        return self.state(sid)

    def state(self, sid):
        with self.connect() as db:
            row = db.execute("SELECT payload FROM team_sessions WHERE id=?", (sid,)).fetchone()
        if row is None:
            raise BrainError("unknown_session")
        return json.loads(row[0])

    def save(self, sid, payload):
        with self.connect() as db:
            db.execute("UPDATE team_sessions SET payload=? WHERE id=?",
                       (json.dumps(payload, ensure_ascii=False, allow_nan=False), sid))

    def task(self, sid, payload):
        with self.connect() as db:
            previous = db.execute("SELECT sid FROM team_tasks WHERE id=?",
                                  (payload["task_id"],)).fetchone()
            if previous and previous[0] != sid:
                raise BrainError("task_scope_mismatch")
            db.execute(
                "INSERT INTO team_tasks VALUES(?,?,?) ON CONFLICT(id) DO UPDATE "
                "SET payload=excluded.payload",
                (payload["task_id"], sid, json.dumps(payload, ensure_ascii=False, allow_nan=False)),
            )
        return payload

    def tasks(self, sid):
        with self.connect() as db:
            return [json.loads(r[0]) for r in db.execute(
                "SELECT payload FROM team_tasks WHERE sid=? ORDER BY rowid", (sid,),
            )]

    def event(self, task_id, payload):
        with self.connect() as db:
            db.execute("INSERT INTO team_events(task_id,payload) VALUES(?,?)",
                       (task_id, json.dumps({"at": now(), **payload}, ensure_ascii=False)))

    def events(self, task_id):
        with self.connect() as db:
            return [json.loads(r[0]) for r in db.execute(
                "SELECT payload FROM team_events WHERE task_id=? ORDER BY id", (task_id,),
            )]

    def delete(self, sid):
        with self.connect() as db:
            db.execute("DELETE FROM team_events WHERE task_id IN "
                       "(SELECT id FROM team_tasks WHERE sid=?)", (sid,))
            db.execute("DELETE FROM team_tasks WHERE sid=?", (sid,))
            db.execute("DELETE FROM team_sessions WHERE id=?", (sid,))

    def delete_task(self, sid, task_id):
        with self.connect() as db:
            row = db.execute("SELECT sid FROM team_tasks WHERE id=?", (task_id,)).fetchone()
            if row is None or row[0] != sid:
                raise BrainError("unknown_task")
            db.execute("DELETE FROM team_events WHERE task_id=?", (task_id,))
            db.execute("DELETE FROM team_tasks WHERE id=? AND sid=?", (task_id, sid))
