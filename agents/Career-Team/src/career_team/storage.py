import json
import os
import re
import shutil
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .models import BrainError, now, uid


def dumps(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def valid_id(value):
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", value):
        raise BrainError("invalid_id", "ID 只能包含字母、数字、下划线和连字符")
    return value


class Store:
    def __init__(self, root: Path):
        self.root = root.expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.root / "leader.sqlite3"
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS sessions(
                    id TEXT PRIMARY KEY, created_at TEXT NOT NULL, state TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS records(
                    id TEXT PRIMARY KEY, sid TEXT NOT NULL REFERENCES sessions(id)
                    ON DELETE CASCADE, kind TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS messages(
                    id INTEGER PRIMARY KEY, sid TEXT REFERENCES sessions(id) ON DELETE CASCADE,
                    role TEXT NOT NULL, content TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events(
                    id INTEGER PRIMARY KEY, sid TEXT REFERENCES sessions(id) ON DELETE CASCADE,
                    at TEXT NOT NULL, tool TEXT NOT NULL, status TEXT NOT NULL,
                    detail TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS links(
                    sid TEXT REFERENCES sessions(id) ON DELETE CASCADE,
                    src TEXT, relation TEXT, dst TEXT, evidence TEXT,
                    PRIMARY KEY(sid,src,relation,dst));
            """)
        os.chmod(self.path, 0o600)

    @contextmanager
    def connect(self):
        with sqlite3.connect(self.path, timeout=30) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA secure_delete=ON")
            yield db

    def session(self, sid=None):
        sid = valid_id(sid or uid())
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO sessions VALUES(?,?,?)", (
                sid, now(), dumps({
                    "remote_allowed": False, "adopted_resume": None, "target": None,
                    "interview": None, "latest_questions": None, "contacts": {},
                    "tasks": [], "candidate_memory": [],
                }),
            ))
        self.directory(sid)
        return sid

    def directory(self, sid):
        path = self.root / "sessions" / valid_id(sid)
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        return path

    def state(self, sid):
        with self.connect() as db:
            row = db.execute("SELECT state FROM sessions WHERE id=?", (sid,)).fetchone()
        if row is None:
            raise BrainError("unknown_session")
        return json.loads(row["state"])

    def save(self, sid, state):
        with self.connect() as db:
            db.execute("UPDATE sessions SET state=? WHERE id=?", (dumps(state), sid))

    def put(self, sid, kind, payload, *, record_id=None, mutable=False):
        record_id = record_id or uid(kind + "_")
        payload = {**payload, "id": record_id}
        with self.connect() as db:
            if mutable:
                existing = db.execute("SELECT sid,kind FROM records WHERE id=?", (record_id,)).fetchone()
                if existing and (existing["sid"] != sid or existing["kind"] != kind):
                    raise BrainError("record_scope_mismatch")
                db.execute("INSERT INTO records VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE "
                           "SET payload=excluded.payload", (record_id, sid, kind, dumps(payload)))
            else:
                db.execute("INSERT INTO records VALUES(?,?,?,?)",
                           (record_id, sid, kind, dumps(payload)))
        return payload

    def get(self, sid, record_id, kind=None):
        with self.connect() as db:
            row = db.execute(
                "SELECT kind,payload FROM records WHERE sid=? AND id=?", (sid, record_id),
            ).fetchone()
        if row is None or (kind and row["kind"] != kind):
            raise BrainError("unknown_record", "未找到当前会话中的记录")
        return json.loads(row["payload"])

    def records(self, sid, kind):
        with self.connect() as db:
            rows = db.execute(
                "SELECT payload FROM records WHERE sid=? AND kind=? ORDER BY rowid", (sid, kind),
            ).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def kind(self, sid, record_id):
        with self.connect() as db:
            row = db.execute("SELECT kind FROM records WHERE sid=? AND id=?",
                             (sid, record_id)).fetchone()
        if row is None:
            raise BrainError("unknown_record")
        return row["kind"]

    def history(self, sid):
        with self.connect() as db:
            rows = db.execute(
                "SELECT role,content FROM messages WHERE sid=? ORDER BY id", (sid,),
            ).fetchall()
        return [dict(r) for r in rows][-20:]

    def turn(self, sid, question, answer):
        with self.connect() as db:
            db.executemany(
                "INSERT INTO messages(sid,role,content) VALUES(?,?,?)",
                [(sid, "user", question), (sid, "assistant", answer)],
            )

    def event(self, sid, tool, status, detail=None):
        with self.connect() as db:
            db.execute(
                "INSERT INTO events(sid,at,tool,status,detail) VALUES(?,?,?,?,?)",
                (sid, now(), tool, status, dumps(detail or {})),
            )

    def link(self, sid, src, relation, dst, evidence):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO links VALUES(?,?,?,?,?)",
                       (sid, src, relation, dst, dumps(evidence)))

    def links(self, sid, topic=None):
        with self.connect() as db:
            rows = db.execute("SELECT src,relation,dst,evidence FROM links WHERE sid=?",
                              (sid,)).fetchall()
        result = [{**dict(r), "evidence": json.loads(r["evidence"])} for r in rows]
        return [r for r in result if not topic or topic.casefold() in
                (r["src"] + " " + r["dst"]).casefold()]

    def sessions(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT id,created_at FROM sessions")]

    def delete(self, sid):
        self.state(sid)
        with self.connect() as db:
            db.execute("DELETE FROM sessions WHERE id=?", (sid,))
        path = self.root / "sessions" / valid_id(sid)
        if path.exists():
            shutil.rmtree(path)


class Memory:
    """Only explicit human confirmation writes to this cross-session store."""

    def __init__(self, root: Path, scope="career"):
        self.scope = scope
        self.root = root.expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.root / "profile.sqlite3"
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS memories("
                       "id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
        os.chmod(self.path, 0o600)

    @contextmanager
    def connect(self):
        with sqlite3.connect(self.path, timeout=30) as db:
            db.execute("PRAGMA secure_delete=ON")
            yield db

    def all(self):
        with self.connect() as db:
            values = [json.loads(r[0]) for r in db.execute("SELECT payload FROM memories")]
        return [m for m in values if m.get("scope", "career") == self.scope]

    def confirm(self, candidate):
        value = {**candidate, "scope": self.scope, "confirmed_at": now()}
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO memories VALUES(?,?)",
                       (candidate["id"], dumps(value)))
        return value

    def delete(self, record_id):
        if record_id not in {m["id"] for m in self.all()}:
            return
        with self.connect() as db:
            db.execute("DELETE FROM memories WHERE id=?", (record_id,))

    def delete_session(self, sid):
        ids = [m["id"] for m in self.all() if m.get("source_session") == sid]
        with self.connect() as db:
            db.executemany("DELETE FROM memories WHERE id=?", [(i,) for i in ids])
