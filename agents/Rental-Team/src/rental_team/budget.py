import asyncio
import os
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

from javis_core.models import BrainError, now


class DailyQuota:
    def __init__(self, path, limit=30):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.limit = min(limit, 30)
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS usage(day TEXT PRIMARY KEY, used INTEGER)")
        os.chmod(self.path, 0o600)

    def reserve(self, count=1):
        day = now()[:10]
        with sqlite3.connect(self.path, timeout=30) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT OR IGNORE INTO usage VALUES(?,0)", (day,))
            used = db.execute("SELECT used FROM usage WHERE day=?", (day,)).fetchone()[0]
            if used + count > self.limit:
                raise BrainError("quota_exhausted")
            db.execute("UPDATE usage SET used=used+? WHERE day=?", (count, day))
        return used + count

    def used(self):
        with sqlite3.connect(self.path) as db:
            row = db.execute("SELECT used FROM usage WHERE day=?", (now()[:10],)).fetchone()
        return row[0] if row else 0

    def exhaust(self):
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO usage VALUES(?,?) ON CONFLICT(day) DO UPDATE "
                       "SET used=MAX(used,excluded.used)", (now()[:10], self.limit))


@dataclass
class Budget:
    quota: DailyQuota
    max_vigo: int = 5
    seconds: float = 180
    interval: float = 3
    started: float = field(default_factory=time.monotonic)
    vigo_calls: int = 0
    calls: dict = field(default_factory=dict)
    last: dict = field(default_factory=dict)
    cancelled: bool = False
    on_checkpoint: object = None

    def checkpoint(self, records, issues):
        if self.on_checkpoint:
            self.on_checkpoint(records, issues)

    def check(self):
        if self.cancelled:
            raise BrainError("cancelled")
        if time.monotonic() - self.started >= self.seconds:
            raise BrainError("task_timeout")

    async def before(self, source, *, business=True):
        self.check()
        await asyncio.sleep(max(0, self.interval - (
            time.monotonic() - self.last.get(source, 0)
        )))
        self.check()
        if source == "vigolive":
            if business and self.vigo_calls >= self.max_vigo:
                raise BrainError("task_budget_exhausted")
            self.quota.reserve()
            if business:
                self.vigo_calls += 1
        self.calls[source] = self.calls.get(source, 0) + 1
        self.last[source] = time.monotonic()
