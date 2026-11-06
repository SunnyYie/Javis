import copy
import json
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest

from brain_leader.daily import TZ, Daily, DailyDraft, DailySettings, DailyStore, DailyVerification
from brain_leader.daily_topics import TOPICS, WeeklyTopics, week_start
from brain_leader.models import BrainError

MONDAY = date(2026, 11, 9)


@pytest.fixture
def store(tmp_path):
    value = DailyStore(tmp_path)
    yield value
    value.close()


def snapshot(day, category, **extra):
    return {
        "day": day.isoformat(), "category": category, "field": TOPICS[category][0],
        "week_start": week_start(day).isoformat(), "question": "合成技术题，仅用于排期测试。",
        **extra,
    }


def test_exactly_five_requested_categories():
    assert {value[0] for value in TOPICS.values()} == {
        "RAG", "LangChain", "Harness engineering", "Claude Code底层实现", "Multi-Agent",
    }


def test_week_plan_is_a_randomized_permutation_saved_only_once(store, monkeypatch):
    calls = []

    class Random:
        def shuffle(self, values):
            calls.append(list(values))
            values[:] = values[1:] + values[:1]

    monkeypatch.setattr("brain_leader.daily_topics.SystemRandom", Random)
    topics = WeeklyTopics(store.db)
    plan = topics.plan(MONDAY)
    assert len(plan) == 5 and {item["category"] for item in plan} == set(TOPICS)
    assert [item["category"] for item in plan] != list(TOPICS)
    assert [item["day"] for item in plan] == [
        (MONDAY + timedelta(days=i)).isoformat() for i in range(5)
    ]
    assert topics.plan(MONDAY + timedelta(days=4)) == plan
    assert len(calls) == 1
    topics.plan(MONDAY + timedelta(days=7))
    assert len(calls) == 2


def test_order_survives_restart_and_out_of_order_previews(store):
    topics = WeeklyTopics(store.db)
    friday = topics.category_for(MONDAY + timedelta(days=4))
    plan = topics.plan(MONDAY)
    reopened = DailyStore(store.root)
    try:
        restored = WeeklyTopics(reopened.db)
        assert restored.category_for(MONDAY + timedelta(days=4)) == friday
        assert restored.plan(MONDAY) == plan
        assert [restored.category_for(MONDAY + timedelta(days=i)) for i in range(5)] == [
            item["category"] for item in plan
        ]
    finally:
        reopened.close()


@pytest.mark.parametrize(("day", "monday"), [
    (date(2026, 11, 8), date(2026, 11, 2)),
    (date(2026, 11, 9), date(2026, 11, 9)),
    (date(2027, 1, 1), date(2026, 12, 28)),
    (date(2027, 1, 4), date(2027, 1, 4)),
])
def test_calendar_week_boundaries(day, monday):
    assert week_start(day) == monday


@pytest.mark.parametrize("offset", [5, 6])
def test_no_weekend_category(store, offset):
    with pytest.raises(BrainError, match="daily_weekday_required"):
        WeeklyTopics(store.db).category_for(MONDAY + timedelta(days=offset))


@pytest.mark.parametrize("status", ["prepared", "sent", "sending", "unknown", "rejected"])
def test_bootstrap_pins_existing_allowed_category_without_changing_snapshot(store, status):
    day = MONDAY + timedelta(days=2)
    original = snapshot(day, "rag")
    store.prepare(original)
    store.update(day, status)
    before = store.get(day)
    plan = WeeklyTopics(store.db).plan(day)
    assert plan[2]["category"] == "rag"
    assert len({item["category"] for item in plan}) == 5
    assert store.get(day) == before


def test_bootstrap_legacy_allowed_label_and_preserves_old_out_of_scope_receipts(store):
    record = snapshot(MONDAY, "langchain")
    record.pop("category")
    store.prepare(record)
    store.update(MONDAY, "sent", message_id="om_original")
    old_day = MONDAY + timedelta(days=1)
    store.prepare({"day": old_day.isoformat(), "field": "工作流与状态", "question": "旧题"})
    store.update(old_day, "sent", message_id="om_old")
    before = store.get(old_day)
    assert WeeklyTopics(store.db).plan(MONDAY)[0]["category"] == "langchain"
    assert store.get(old_day) == before


def test_existing_duplicate_week_categories_fail_closed(store):
    for offset in (0, 1):
        day = MONDAY + timedelta(days=offset)
        store.prepare(snapshot(day, "rag"))
        store.update(day, "sent")
    with pytest.raises(BrainError, match="daily_weekly_category_conflict"):
        WeeklyTopics(store.db).plan(MONDAY)


@pytest.mark.parametrize("case", ["category", "field", "week_start"])
def test_prepared_snapshot_cannot_override_scheduled_category(store, case):
    topics = WeeklyTopics(store.db)
    category = topics.category_for(MONDAY)
    record = snapshot(MONDAY, category)
    record[case] = "invalid"
    with pytest.raises(BrainError) as caught:
        topics.validate(MONDAY, record)
    assert caught.value.code == "daily_weekly_snapshot_conflict"


@pytest.mark.parametrize("status", ["sent", "sending", "unknown"])
def test_used_or_ambiguous_category_cannot_be_sent_on_another_day(store, status):
    topics = WeeklyTopics(store.db)
    category = topics.category_for(MONDAY)
    other = MONDAY + timedelta(days=1)
    store.prepare(snapshot(other, category))
    store.update(other, status)
    with pytest.raises(BrainError) as caught:
        topics.validate(MONDAY, snapshot(MONDAY, category))
    assert caught.value.code == "daily_weekly_category_used"


def test_partial_or_corrupt_weekly_plan_is_not_silently_reshuffled(store):
    topics = WeeklyTopics(store.db)
    topics.plan(MONDAY)
    with store.db:
        store.db.execute("DELETE FROM daily_weekly_topics WHERE weekday=4")
    with pytest.raises(BrainError, match="daily_weekly_plan_invalid"):
        topics.plan(MONDAY)


def test_source_failure_preserves_scheduled_category_instead_of_switching(store):
    topics = WeeklyTopics(store.db)
    category = topics.category_for(MONDAY)
    calls = []
    adapters = SimpleNamespace(daily_evidence=lambda query: calls.append(query) or {
        "corpus_id": "test", "version": 1, "chunks": [],
    })
    daily = Daily(store, None, adapters, None, None)
    daily.config = SimpleNamespace(web_search=False)
    for _ in range(2):
        with pytest.raises(BrainError) as caught:
            daily.prepare(MONDAY)
        assert caught.value.code == "daily_evidence_insufficient"
    assert calls == [TOPICS[category][1], TOPICS[category][1]]
    assert topics.category_for(MONDAY) == category
    assert store.get(MONDAY) is None


def test_used_category_is_blocked_before_remote_model_or_retrieval(store):
    topics = WeeklyTopics(store.db)
    category = topics.category_for(MONDAY)
    other = MONDAY + timedelta(days=1)
    store.prepare(snapshot(other, category))
    store.update(other, "unknown")
    with pytest.raises(BrainError) as caught:
        Daily(store, None, None, None, None).prepare(MONDAY)
    assert caught.value.code == "daily_weekly_category_used"


def test_full_week_has_five_distinct_categories_and_retry_does_not_use_another(store):
    import time

    quote = "Persist the validated technical state before continuing the next operation."
    model_calls, sent = [], []

    class Models:
        def structured(self, schema, task, payload):
            model_calls.append(copy.deepcopy(payload))
            if schema is DailyDraft:
                return DailyDraft(
                    question=f"在 {payload['field']} 中，如何解释有依据的工程机制与使用边界？",
                    answer=[{"text": "先验证状态，再保存状态，最后继续后续操作。", "evidence_ids": ["e1"]}],
                    quotes=[{"evidence_id": "e1", "quote": quote}],
                )
            return DailyVerification(
                relevant_to_topic=True, supported_answer_points=[1],
                question_supported=True, neutral_technical_content=True,
            )

    adapters = SimpleNamespace(daily_evidence=lambda query: {
        "corpus_id": "test", "version": 1,
        "chunks": [{
            "id": "e1", "text": quote, "url": "https://example.test/agent/state",
            "expires_at": time.time() + 3600,
        }],
    })
    bot = SimpleNamespace(
        authenticate=lambda: None,
        send=lambda content, delivery_id: sent.append((content, delivery_id)) or "om_test",
    )
    settings = DailySettings(
        _env_file=None, enabled=True, app_id="cli_test", app_secret="test-only",
        recipient_email="daily@example.test", web_search=False,
    )
    daily = Daily(store, settings, adapters, Models(), bot)
    categories = []
    for offset in range(5):
        day = MONDAY + timedelta(days=offset)
        now = datetime(day.year, day.month, day.day, 11, tzinfo=TZ)
        assert daily.run(now)["status"] == "sent"
        assert daily.run(now)["status"] == "already_sent"
        categories.append(json.loads(store.get(day)["artifact"])["category"])
    assert set(categories) == set(TOPICS)
    assert len(sent) == 5 and len(model_calls) == 10
    assert all("scope" in payload for payload in model_calls)
