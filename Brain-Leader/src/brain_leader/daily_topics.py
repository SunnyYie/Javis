"""Persist one genuinely random five-category permutation per Shanghai calendar week."""

import json
from datetime import date, timedelta
from random import SystemRandom

from .models import BrainError

TOPICS = {
    "rag": ("RAG", "RAG retrieval augmented generation chunking retrieval reranking grounding"),
    "langchain": ("LangChain", "LangChain framework agents tools middleware structured output LangGraph"),
    "harness_engineering": (
        "Harness engineering",
        "agent harness engineering execution environment evaluation feedback context tools guardrails",
    ),
    "claude_code_internals": (
        "Claude Code底层实现",
        "Claude Code publicly documented architecture agent loop tools context permissions "
        "公开文档与公开代码可证实的机制 不推断闭源内部实现",
    ),
    "multi_agent": (
        "Multi-Agent", "Multi-Agent orchestration collaboration handoff shared state coordination",
    ),
}


def week_start(day):
    return day - timedelta(days=day.weekday())


class WeeklyTopics:
    def __init__(self, db):
        self.db = db
        with db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS daily_weekly_topics(
                    week_start TEXT NOT NULL, weekday INTEGER NOT NULL CHECK(weekday BETWEEN 0 AND 4),
                    category TEXT NOT NULL,
                    PRIMARY KEY(week_start,weekday), UNIQUE(week_start,category)
                )
            """)

    def plan(self, day):
        monday = week_start(day)
        rows = self.db.execute(
            "SELECT weekday,category FROM daily_weekly_topics WHERE week_start=? ORDER BY weekday",
            (monday.isoformat(),),
        ).fetchall()
        if not rows:
            # Pin any existing allowed-category snapshots, including ambiguous deliveries.
            pinned = {}
            labels = {topic[0]: key for key, topic in TOPICS.items()}
            for row in self.db.execute(
                "SELECT day,artifact FROM daily WHERE day>=? AND day<?",
                (monday.isoformat(), (monday + timedelta(days=5)).isoformat()),
            ):
                artifact = json.loads(row["artifact"])
                key = artifact.get("category") or labels.get(artifact.get("field"))
                if key in TOPICS:
                    pinned[date.fromisoformat(row["day"]).weekday()] = key
            if len(set(pinned.values())) != len(pinned):
                raise BrainError("daily_weekly_category_conflict")
            remaining = [key for key in TOPICS if key not in pinned.values()]
            SystemRandom().shuffle(remaining)
            categories = [pinned[i] if i in pinned else remaining.pop() for i in range(5)]
            with self.db:
                self.db.executemany(
                    "INSERT INTO daily_weekly_topics(week_start,weekday,category) VALUES(?,?,?)",
                    [(monday.isoformat(), i, key) for i, key in enumerate(categories)],
                )
            rows = list(enumerate(categories))
        if (
            [row[0] for row in rows] != list(range(5))
            or {row[1] for row in rows} != set(TOPICS)
        ):
            raise BrainError("daily_weekly_plan_invalid")
        return [
            {"day": (monday + timedelta(days=row[0])).isoformat(), "category": row[1],
             "field": TOPICS[row[1]][0]}
            for row in rows
        ]

    def category_for(self, day):
        if day.weekday() >= 5:
            raise BrainError("daily_weekday_required")
        return self.plan(day)[day.weekday()]["category"]

    def validate(self, day, artifact):
        category = self.category_for(day)
        if (
            artifact.get("field") != TOPICS[category][0]
            or artifact.get("category", category) != category
            or artifact.get("week_start", week_start(day).isoformat()) != week_start(day).isoformat()
        ):
            raise BrainError("daily_weekly_snapshot_conflict", "已有题目与每周五类规则冲突，未发送")
        monday = week_start(day)
        for row in self.db.execute(
            "SELECT artifact FROM daily WHERE day>=? AND day<? AND day<>?"
            " AND status IN ('sent','sending','unknown')",
            (monday.isoformat(), (monday + timedelta(days=7)).isoformat(), day.isoformat()),
        ):
            previous = json.loads(row["artifact"])
            if previous.get("category") == category or previous.get("field") == TOPICS[category][0]:
                raise BrainError("daily_weekly_category_used", "本周同类题已发送或结果不确定，未重复发送")
