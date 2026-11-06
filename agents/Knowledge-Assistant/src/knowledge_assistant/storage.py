import json
import os
import sqlite3
import tempfile
import time
import uuid
from pathlib import Path

from .models import Bundle, Chunk, digest


class Store:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        (root / "snapshots").mkdir(exist_ok=True)
        self.db = sqlite3.connect(root / "knowledge.sqlite3", timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
            INSERT OR IGNORE INTO meta VALUES('version','0');
            CREATE TABLE IF NOT EXISTS sources(
                source TEXT PRIMARY KEY, repo TEXT, commit_id TEXT, checked_at REAL,
                expires_at REAL, coverage TEXT, status TEXT DEFAULT 'ok'
            );
            CREATE TABLE IF NOT EXISTS chunks(
                id TEXT PRIMARY KEY, payload TEXT NOT NULL, vector TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS active(
                source TEXT, chunk_id TEXT REFERENCES chunks(id),
                PRIMARY KEY(source, chunk_id)
            );
            CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, created_at REAL);
            CREATE TABLE IF NOT EXISTS turns(
                id INTEGER PRIMARY KEY, session TEXT REFERENCES sessions(id),
                question TEXT, answer TEXT, created_at REAL
            );
            CREATE TABLE IF NOT EXISTS events(
                id INTEGER PRIMARY KEY, source TEXT, category TEXT, created_at REAL
            );
        """)
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO meta VALUES('corpus_id',?)", (uuid.uuid4().hex,))

    @property
    def version(self) -> int:
        return int(self.db.execute("SELECT value FROM meta WHERE key='version'").fetchone()[0])

    @property
    def corpus_id(self) -> str:
        return self.db.execute("SELECT value FROM meta WHERE key='corpus_id'").fetchone()[0]

    def source(self, url: str) -> dict | None:
        row = self.db.execute("SELECT * FROM sources WHERE source=?", (url,)).fetchone()
        return dict(row) if row else None

    def sources(self) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM sources ORDER BY source")]

    def active_chunks(self) -> list[Chunk]:
        return [
            Chunk(**json.loads(r[0]))
            for r in self.db.execute(
                "SELECT payload FROM chunks JOIN active ON chunks.id=active.chunk_id"
            )
        ]

    def vectors_by_content(self, source: str) -> dict[str, list[float]]:
        rows = self.db.execute(
            "SELECT payload,vector FROM chunks JOIN active ON chunks.id=active.chunk_id "
            "WHERE active.source=?",
            (source,),
        )
        return {json.loads(r[0])["content_hash"]: json.loads(r[1]) for r in rows}

    def check_embedding(self, fingerprint: str):
        row = self.db.execute("SELECT value FROM meta WHERE key='embedding'").fetchone()
        if row and row[0] != fingerprint:
            raise RuntimeError("Embedding changed: use a new DATA_DIR and rebuild the index.")

    def publish(self, bundle: Bundle, chunks: list[Chunk], vectors: list, fingerprint: str):
        if not bundle.complete:
            raise RuntimeError("Incomplete source cannot replace the active snapshot.")
        self.check_embedding(fingerprint)
        snapshot = digest([d.__dict__ for d in bundle.documents])
        path = self.root / "snapshots" / f"{snapshot}.json"
        # A snapshot is durable before the SQLite manifest can reference its content.
        temp_name = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                delete=False,
            ) as temp:
                temp_name = temp.name
                json.dump([d.__dict__ for d in bundle.documents], temp, ensure_ascii=False)
                temp.flush()
                os.fsync(temp.fileno())
            os.replace(temp_name, path)
        finally:
            if temp_name:
                Path(temp_name).unlink(missing_ok=True)
        coverage = dict(bundle.coverage, snapshot=snapshot)

        def signature(c):
            return c.id, c.role, c.license, c.url

        before = {
            signature(Chunk(**json.loads(r[0])))
            for r in self.db.execute(
                "SELECT payload FROM chunks JOIN active ON chunks.id=active.chunk_id "
                "WHERE active.source=?",
                (bundle.source,),
            )
        }
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO meta VALUES('embedding',?)", (fingerprint,))
            for c, v in zip(chunks, vectors, strict=True):
                self.db.execute(
                    "INSERT OR REPLACE INTO chunks VALUES(?,?,?)",
                    (c.id, json.dumps(c.to_dict(), ensure_ascii=False), json.dumps(v)),
                )
            self.db.execute("DELETE FROM active WHERE source=?", (bundle.source,))
            self.db.executemany(
                "INSERT INTO active VALUES(?,?)", [(bundle.source, c.id) for c in chunks]
            )
            now = time.time()
            expires = min((c.expires_at for c in chunks), default=now)
            self.db.execute(
                "INSERT OR REPLACE INTO sources VALUES(?,?,?,?,?,?,?)",
                (
                    bundle.source,
                    bundle.repo,
                    bundle.commit,
                    now,
                    expires,
                    json.dumps(coverage, ensure_ascii=False),
                    "ok",
                ),
            )
            if before != {signature(c) for c in chunks}:
                self.db.execute(
                    "UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='version'"
                )

    def event(self, source: str, category: str):
        with self.db:
            self.db.execute(
                "INSERT INTO events(source,category,created_at) VALUES(?,?,?)",
                (source, category, time.time()),
            )
            self.db.execute("UPDATE sources SET status=? WHERE source=?", (category, source))

    def new_session(self) -> str:
        sid = uuid.uuid4().hex[:12]
        with self.db:
            self.db.execute("INSERT INTO sessions VALUES(?,?)", (sid, time.time()))
        return sid

    def sessions(self) -> list[str]:
        return [r[0] for r in self.db.execute("SELECT id FROM sessions ORDER BY created_at DESC")]

    def history(self, sid: str, limit: int = 6) -> list[dict]:
        rows = self.db.execute(
            "SELECT question,answer FROM turns WHERE session=? ORDER BY id DESC LIMIT ?",
            (sid, limit),
        ).fetchall()
        return [dict(r) for r in reversed(rows)]

    def add_turn(self, sid: str, question: str, answer: str):
        with self.db:
            self.db.execute(
                "INSERT INTO turns(session,question,answer,created_at) VALUES(?,?,?,?)",
                (sid, question, answer, time.time()),
            )
