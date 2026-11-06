import copy
import json
from datetime import date
from types import SimpleNamespace

import httpx
import pytest
from javis_tools.feishu import FeishuBot, FeishuError
from javis_tools.web_reading import article_url, search_articles

from brain_leader.daily import Daily, DailyStore, enhance
from brain_leader.daily_card import escape, question_elements, render
from brain_leader.daily_reading import ReadingSelection, related_readings
from brain_leader.models import BrainError


def artifact():
    return {
        "day": "2026-11-06", "field": "工作流与状态", "question": "Agent 如何从 checkpoint 恢复执行？",
        "answer": [{"text": "恢复已保存状态，并继续后续节点。", "evidence_ids": ["e1"]}],
        "sources": [{
            "id": "e1", "url": "https://docs.example.test/agent/replay#checkpoint",
            "path": "replay.md", "text": "Persist state before resuming execution.",
        }],
        "readings": [
            {"title": "Checkpoint 实践", "url": "https://blog.example.test/checkpoint",
             "reason": "介绍恢复工作流时的检查点边界。"},
            {"title": "持久化指南", "url": "https://docs.example.test/persistence",
             "reason": "介绍状态保存和恢复的机制。"},
        ],
    }


def article(index):
    return {
        "id": f"web:{index}", "url": f"https://blog.example.test/checkpoint-{index}",
        "title": f"Checkpoint 教程 {index}", "text": "Persist state before resuming execution.",
        "fetched_at": 1234,
    }


class Articles:
    def __init__(self, items=None):
        self.items = [article(1), article(2), article(3)] if items is None else items
        self.queries = []

    def daily_articles(self, query):
        self.queries.append(query)
        return {"articles": self.items, "warnings": []}


class Selection:
    def __init__(self, count=2):
        self.count = count
        self.bad_quote = False

    def structured(self, schema, task, payload):
        assert schema is ReadingSelection
        return ReadingSelection(articles=[{
            "article_id": item["id"], "reason": "介绍恢复工作流时的状态持久化边界。",
            "quote": "Unrelated invented quote." if self.bad_quote else item["text"],
        } for item in payload["articles"][:self.count]])


def test_card_hierarchy_answer_citations_and_two_article_links():
    card = render(artifact())
    assert card["schema"] == "2.0"
    assert card["config"]["width_mode"] == "default"
    assert card["header"]["subtitle"]["content"] == "2026-11-06 · 工作流与状态"
    blocks = card["body"]["elements"]
    assert len(blocks) == 3
    question = blocks[0]["columns"][0]
    assert question["background_style"] == "blue-50"
    assert question["elements"][1]["text_size"] == "question"
    answer = blocks[1]["columns"][0]["elements"]
    assert answer[1]["text_size"] == "normal"
    assert answer[2]["text_size"] == "caption"
    assert "https://docs.example.test/agent/replay#checkpoint" in answer[2]["content"]
    assert "来源：" in answer[2]["content"]
    text = json.dumps(card, ensure_ascii=False)
    assert "题目与答案基于" not in text and "来自本地知识库" not in text
    assert text.count("https://blog.example.test/checkpoint") == 1


def test_untrusted_titles_cannot_create_mentions_links_or_card_components():
    record = artifact()
    record["readings"][0]["title"] = "<at id=all></at> [spoof](https://evil.example)"
    card = render(record)
    reading = card["body"]["elements"][2]["columns"][0]["elements"][1]["content"]
    assert "<at " not in reading
    assert "[spoof]" not in reading
    assert "&#91;spoof&#93;" in reading
    assert reading.endswith("(https://blog.example.test/checkpoint)")


def test_compound_question_stem_is_larger_than_each_subquestion_without_rewriting():
    record = artifact()
    record["question"] = (
        "在 LangGraph 基于 checkpoint 的时间旅行中："
        "(1) Replay 与 Fork 各是什么，共同机制是什么？"
        "(2) 从某个 checkpoint 回放时，哪些节点会重新执行、哪些不会，"
        "为什么 LLM 调用结果可能与上次不同？"
        "(3) 如何借助 get_state_history 定位并回放特定 checkpoint？"
        "(4) 用 update_state 做 Fork 时 as_node 的默认推断规则是什么，"
        "哪三种情况必须显式指定 as_node？请说明原理与实际边界。"
    )
    snapshot = copy.deepcopy(record)
    elements = render(record)["body"]["elements"][0]["columns"][0]["elements"]
    assert len(elements) == 6
    assert elements[1] == {
        "tag": "markdown", "text_size": "question",
        "content": "**在 LangGraph 基于 checkpoint 的时间旅行中：**",
    }
    assert all(item["text_size"] == "normal" for item in elements[2:])
    assert elements[2]["content"] == "**&#40;1&#41;** Replay 与 Fork 各是什么，共同机制是什么？"
    assert "get&#95;state&#95;history" in elements[4]["content"]
    assert "&&#35;" not in json.dumps(elements)
    assert elements[5]["content"].endswith("请说明原理与实际边界。")
    assert record == snapshot


@pytest.mark.parametrize(("text", "stem", "markers", "parts"), [
    ("状态恢复：（1）如何保存？（2）如何继续？", "状态恢复：", ["（1）", "（2）"],
     ["如何保存？", "如何继续？"]),
    ("状态恢复：\n1. 如何保存？\n2. 如何继续？", "状态恢复：", ["1.", "2."],
     ["如何保存？", "如何继续？"]),
    ("状态恢复：\n1、 如何保存？\n2、 如何继续？", "状态恢复：", ["1、", "2、"],
     ["如何保存？", "如何继续？"]),
    ("(1) 如何保存？(2) 如何继续？", "", ["(1)", "(2)"], ["如何保存？", "如何继续？"]),
])
def test_numbered_question_variants(text, stem, markers, parts):
    elements = question_elements(text)
    if stem:
        assert elements[1]["content"] == "**" + escape(stem) + "**"
    items = elements[2:] if stem else elements[1:]
    assert [item["content"] for item in items] == [
        "**" + escape(marker) + "** " + escape(part)
        for marker, part in zip(markers, parts, strict=True)
    ]
    assert all(item["text_size"] == "normal" for item in items)


@pytest.mark.parametrize("text", [
    "invoke(1) 与 invoke(2) 有什么区别？",
    "版本 1.2 和 2.3 有什么区别？",
    "保存状态：(1) 如何保存？",
    "保存状态：(1) 如何保存？(3) 如何继续？",
    "保存状态：(1) 如何保存？(1) 如何继续？",
    "保存状态：(1) 如何保存？(2)",
    "保存状态：(1）如何保存？（2) 如何继续？",
])
def test_ambiguous_numbers_do_not_split_question(text):
    assert question_elements(text)[1:] == [
        {"tag": "markdown", "text_size": "question", "content": "**" + escape(text) + "**"},
    ]


def test_subquestions_escape_untrusted_markup():
    elements = question_elements("Agent：(1) <at id=all></at>？(2) [spoof](https://evil.example)？")
    assert "<at " not in elements[2]["content"]
    assert "[spoof]" not in elements[3]["content"]


def test_article_selection_has_real_quotes_and_two_or_three_results():
    adapters = Articles()
    result, warnings = related_readings(adapters, Selection(3), artifact())
    assert len(result) == 3 and not warnings
    assert all(item["quote"] == article(1)["text"] for item in result)
    assert "checkpoint" in adapters.queries[0]


def test_bad_quotes_are_not_published_as_reading_recommendations():
    model = Selection()
    model.bad_quote = True
    result, warnings = related_readings(Articles(), model, artifact())
    assert not result and "related_article_selection_invalid" in warnings


def test_search_failure_does_not_fake_readings_or_remove_answer_sources():
    class Failing:
        def daily_articles(self, query):
            raise RuntimeError("PRIVATE_API_KEY")

    original = artifact()
    original.pop("readings")
    updated = enhance(Failing(), Selection(), original)
    assert updated["readings"] == []
    assert updated["reading_warnings"] == ["article_search_failed"]
    assert "https://docs.example.test/agent/replay#checkpoint" in json.dumps(updated["content"])
    assert "PRIVATE_API_KEY" not in json.dumps(updated)


@pytest.mark.parametrize("url", [
    "http://example.test/article", "https://github.com/org/repo",
    "https://example.test/", "https://example.test/blog",
    "https://example.test/article?key=secret", "https://[broken",
])
def test_not_an_article_candidate(url):
    assert not article_url(url)


def test_disabled_search_or_missing_candidates_does_not_call_selector():
    class Forbidden:
        def structured(self, *args):
            pytest.fail("must not invoke selector without candidate bodies")

    assert related_readings(Articles([]), Forbidden(), artifact())[0] == []
    assert related_readings(None, Forbidden(), artifact(), enabled=False)[0] == []


def test_web_search_reads_bodies_and_titles_before_recommending_not_snippets():
    calls = []
    paragraph = "Persist state in a checkpoint before resuming execution. " * 25
    document = (
        "<html><head><title>Checkpoint guide</title></head><body>"
        "<article><h1>Checkpoint guide</h1><p>" + paragraph + "</p></article></body></html>"
    ).encode()

    class HTTP:
        def json(self, url, **kwargs):
            calls.append(("search", url))
            return {"results": [
                {"url": "https://github.com/org/repo", "content": "Not evidence"},
                {"url": "https://blog.example.test/checkpoint-guide"},
            ]}

        def request(self, method, url, **kwargs):
            calls.append(("read", url))
            assert kwargs["timeout"] == 12 and kwargs["max_bytes"] == 1024 * 1024
            return document, {"content-type": "text/html"}, url

    result = search_articles(SimpleNamespace(
        tavily_api_key=SimpleNamespace(get_secret_value=lambda: "fake-only"),
        search_endpoint="https://search.example.test",
    ), "Agent checkpoint", http=HTTP())
    assert len(result["articles"]) == 1
    item = result["articles"][0]
    assert item["title"] == "Checkpoint guide"
    assert "Persist state" in item["text"]
    assert len(calls) == 2


def test_revision_outbox_keeps_original_snapshot_and_independent_uuid(tmp_path):
    original = DailyStore(tmp_path)
    revision = DailyStore(tmp_path, table="daily_card_v2")
    from datetime import date

    day = date(2026, 11, 6)
    first = artifact()
    second = copy.deepcopy(first)
    second["format_version"] = 2
    try:
        original.prepare(first)
        revision.prepare(second)
        assert original.get(day)["artifact"] != revision.get(day)["artifact"]
        assert original.get(day)["delivery_id"] != revision.get(day)["delivery_id"]
        assert json.loads(original.get(day)["artifact"]) == first
        third = copy.deepcopy(second)
        third["question"] += "请说明边界。"
        revision.revise_prepared(day, third)
        archived = revision.db.execute("SELECT artifact FROM daily_card_history").fetchone()[0]
        assert json.loads(archived) == second
        revision.update(day, "sent", message_id="om_test")
        with pytest.raises(BrainError, match="daily_revision_not_editable"):
            revision.revise_prepared(day, third)
    finally:
        original.close()
        revision.close()


def test_card_is_sent_as_interactive_not_post():
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"code": 0, "data": {"message_id": "om_card_test"}})

    bot = FeishuBot("cli_test", "fake-only", "daily@example.test", client=httpx.Client(
        transport=httpx.MockTransport(handler),
    ))
    bot._token = "fake-only"
    try:
        bot.send(render(artifact()), "test-card-id")
    finally:
        bot.close()
    assert sent[0]["msg_type"] == "interactive"
    assert json.loads(sent[0]["content"])["schema"] == "2.0"


def test_refresh_updates_existing_message_and_preserves_sent_snapshot(tmp_path):
    store = DailyStore(tmp_path)
    original = artifact()
    original["content"] = render(original)
    store.prepare(original)
    store.update(date(2026, 11, 6), "sent", route="test-route", message_id="om_existing")
    before = store.get(date(2026, 11, 6))
    updated = []
    bot = SimpleNamespace(
        authenticate=lambda: None,
        update_card=lambda mid, content: updated.append((mid, content)),
    )
    config = SimpleNamespace(ready=lambda: True, route=lambda: "test-route")
    try:
        result = Daily(store, config, None, None, bot).refresh_layout(date(2026, 11, 6))
        assert result["status"] == "updated" and result["new_message_sent"] is False
        assert updated == [("om_existing", render(original))]
        assert store.get(date(2026, 11, 6)) == before
        rows = store.db.execute("SELECT status FROM daily_layout_updates ORDER BY id").fetchall()
        assert [row[0] for row in rows] == ["updating", "updated"]
    finally:
        store.close()


@pytest.mark.parametrize("case", ["prepared", "unknown", "route", "old_post"])
def test_refresh_requires_confirmed_sent_card_for_same_recipient(tmp_path, case):
    store = DailyStore(tmp_path)
    record = artifact()
    record["content"] = render(record) if case != "old_post" else {}
    store.prepare(record)
    store.update(
        date(2026, 11, 6), case if case in ("prepared", "unknown") else "sent",
        route="other" if case == "route" else "test-route", message_id="om_existing",
    )
    config = SimpleNamespace(ready=lambda: True, route=lambda: "test-route")
    try:
        with pytest.raises(BrainError):
            Daily(store, config, None, None, None).refresh_layout(date(2026, 11, 6))
    finally:
        store.close()


@pytest.mark.parametrize(("status", "body", "error", "uncertain"), [
    (200, {"code": 0}, None, False),
    (400, {"code": 230001}, "feishu_card_update_rejected", False),
    (503, {"code": 230001}, "feishu_card_update_unknown", True),
    (200, {}, "feishu_card_update_unknown", True),
])
def test_card_patch_uses_verified_endpoint_and_does_not_send_new_message(status, body, error, uncertain):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, json=body)

    bot = FeishuBot("cli_test", "fake-only", "daily@example.test", client=httpx.Client(
        transport=httpx.MockTransport(handler),
    ))
    bot._token = "fake-only"
    try:
        if error:
            with pytest.raises(FeishuError) as caught:
                bot.update_card("om_existing", render(artifact()))
            assert caught.value.category == error and caught.value.uncertain == uncertain
        else:
            bot.update_card("om_existing", render(artifact()))
    finally:
        bot.close()
    assert len(calls) == 1
    assert calls[0].method == "PATCH"
    assert str(calls[0].url) == "https://open.feishu.cn/open-apis/im/v1/messages/om_existing"
    assert json.loads(json.loads(calls[0].content)["content"])["schema"] == "2.0"


def test_card_patch_rejects_message_id_path_injection():
    bot = FeishuBot("cli_test", "fake-only", "daily@example.test")
    bot._token = "fake-only"
    try:
        with pytest.raises(FeishuError, match="feishu_card_update_invalid"):
            bot.update_card("om_existing/../../auth", render(artifact()))
    finally:
        bot.close()
