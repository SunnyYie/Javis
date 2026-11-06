import json
from types import SimpleNamespace

import pytest
from conftest import bundle

from interview_question_bank.agent import Assistant
from interview_question_bank.models import (
    Draft,
    InterviewQuestion,
    InterviewRequest,
    ServiceError,
    Verification,
)


def assistant(env):
    return Assistant(
        env.s, store=env.store, knowledge=env.knowledge, models=env.models,
        collectors=env.collectors, cache=env.cache,
    )


def test_default_questions_are_structured_grounded_and_saved(env):
    env.knowledge.ingest(bundle())
    result = assistant(env).ask("LangGraph 面试题", local_only=True)
    assert result.sufficient and result.persisted and result.route == "local"
    assert len(result.questions) == 5 and result.citations
    assert [q["difficulty"] for q in result.questions] == ["基础", "基础", "进阶", "进阶", "高阶"]
    assert env.store.db.execute("SELECT count(*) FROM question_records").fetchone()[0] == 5
    assert env.collectors.calls == []


def test_empty_local_only_does_not_search_or_hallucinate(env):
    result = assistant(env).ask("Redis", local_only=True)
    assert not result.sufficient and not result.questions and not result.citations
    assert not result.persisted
    assert env.collectors.calls == []


def test_online_ingests_then_new_session_reuses_without_external_search(env):
    value = bundle()
    env.collectors.urls = [value.source]
    env.collectors.bundles[value.source] = value
    result = assistant(env).ask("LangGraph")
    assert result.route == "online" and result.sufficient and result.persisted
    assert env.store.version == 1
    env.collectors.calls.clear()
    result = assistant(env).ask("换个问法了解 LangGraph", local_only=True)
    assert result.route == "local" and result.sufficient
    assert env.collectors.calls == []


def test_milvus_outage_is_not_treated_as_empty_corpus(env):
    env.index.available = False
    with pytest.raises(ServiceError, match="milvus"):
        assistant(env).ask("Redis")
    assert not env.collectors.calls


def test_failed_ingest_answers_from_transient_and_never_caches(env):
    value = bundle()
    env.collectors.urls = [value.source]
    env.collectors.bundles[value.source] = value
    env.index.write_failure = True
    result = assistant(env).ask("LangGraph")
    assert result.questions and not result.persisted
    assert not env.cache.rows and env.store.version == 0
    assert any("未保存" in warning for warning in result.warnings)


def test_unknown_evidence_ids_are_discarded(env):
    env.knowledge.ingest(bundle())
    env.models.output = Draft(questions=[InterviewQuestion(
        title="伪造证据题", difficulty="基础", knowledge_points=["Redis"],
        answer="错误", key_points=["错误"], origin="基于资料生成", evidence_ids=["invented"],
    )])
    result = assistant(env).ask("Redis", local_only=True)
    assert not result.questions and not result.sufficient and not env.cache.rows


def test_original_label_requires_exact_quote(env):
    env.knowledge.ingest(bundle())
    cid = env.store.active_chunks()[0].id
    env.models.output = Draft(questions=[InterviewQuestion(
        title="原题？", difficulty="基础", knowledge_points=["LangGraph"], answer="说明",
        key_points=["说明"], origin="来源原题", evidence_ids=[cid],
        source_question_quote="正文中并没有这句话",
    )])
    result = assistant(env).ask("LangGraph", local_only=True)
    assert result.questions[0]["origin"] == "基于资料生成"
    assert not result.sufficient and not env.cache.rows


def test_verifier_removes_unsupported_answers(env):
    env.knowledge.ingest(bundle())
    env.models.verify = lambda *a: Verification(
        sufficient=False, supported_question_numbers=[1], reason="其他答案无依据",
    )
    result = assistant(env).ask("LangGraph", local_only=True)
    assert len(result.questions) == 1 and not result.sufficient
    assert env.models.calls.count("draft") == 2


@pytest.mark.parametrize("initial_sufficient", [False, True])
def test_one_grounded_revision_must_pass_the_same_verifier(env, initial_sufficient):
    env.knowledge.ingest(bundle())
    checks = []

    def verify(request, draft, chunks):
        checks.append(draft)
        return Verification(
            sufficient=True if len(checks) == 2 else initial_sufficient,
            supported_question_numbers=list(range(1, 6)) if len(checks) == 2 else [1],
            reason="" if len(checks) == 2 else "其他题目缺少指定证据",
        )

    env.models.verify = verify
    result = assistant(env).ask("LangGraph", local_only=True)
    assert result.sufficient and len(result.questions) == 5
    assert len(checks) == 2 and env.models.calls.count("draft") == 2


def test_revision_cannot_replace_better_partial_with_inconsistent_success_flag(env):
    env.knowledge.ingest(bundle())
    checks = []

    def verify(*args):
        checks.append(True)
        return Verification(
            sufficient=len(checks) == 2,
            supported_question_numbers=list(range(1, 6)) if len(checks) == 1 else [],
            reason="未全部满足请求",
        )

    env.models.verify = verify
    result = assistant(env).ask("LangGraph", local_only=True)
    assert len(result.questions) == 5 and not result.sufficient
    assert not env.cache.rows and len(checks) == 2


def test_failed_revision_preserves_previously_verified_partial_answer(env):
    env.knowledge.ingest(bundle())
    original = env.models.draft

    def draft(request, chunks, history, feedback=None):
        if feedback:
            raise ServiceError("llm", "timeout")
        return original(request, chunks, history)

    env.models.draft = draft
    env.models.verify = lambda *args: Verification(
        supported_question_numbers=[1], sufficient=False, reason="缺少依据",
    )
    result = assistant(env).ask("LangGraph", local_only=True)
    assert len(result.questions) == 1 and not result.sufficient
    assert not env.cache.rows and any("修订失败" in w for w in result.warnings)


def test_context_followup_uses_saved_structured_questions(env):
    env.knowledge.ingest(bundle())
    first = assistant(env).ask("LangGraph", local_only=True)
    history = [{"question": "LangGraph", "answer": json.dumps(first.to_dict())}]
    request = InterviewRequest(
        query="LangGraph 题目 基础 1", topic="LangGraph", mode="explain", target_number=2,
    )
    env.models.parse = lambda q, h: request
    cid = first.citations[0]["id"]
    env.models.output = Draft(explanation="第二题的说明", explanation_evidence_ids=[cid])
    result = assistant(env).ask("解释第二题", history, local_only=True)
    assert result.explanation and result.sufficient and not result.questions
    request.target_number = 99
    with pytest.raises(ValueError, match="题号不存在"):
        assistant(env).ask("解释第99题", history, local_only=True)


@pytest.mark.parametrize("has_history", [False, True])
def test_contextual_reranking_uses_resolved_topic_instead_of_anaphoric_question(
    env, monkeypatch, has_history,
):
    env.knowledge.ingest(bundle())
    calls = []
    original = env.knowledge.retrieve

    def retrieve(query, **kwargs):
        calls.append((query, kwargs["rerank_query"]))
        return original(query, **kwargs)

    monkeypatch.setattr(env.knowledge, "retrieve", retrieve)
    request = InterviewRequest(query="LangGraph termination and loop control", topic="LangGraph")
    assistant(env).retrieve({
        "request": request, "question": "解释第二题", "local_only": True,
        "history": [{"question": "LangGraph"}] if has_history else [],
    })
    assert calls == [(request.query, request.query if has_history else "解释第二题")]


@pytest.mark.parametrize("question", ["", "21题", "给我0道题", "x" * 8001])
def test_bad_input_rejected(env, question):
    with pytest.raises(ValueError):
        assistant(env).ask(question)


def test_each_online_round_has_at_most_five_body_attempts(env):
    env.collectors.urls = [f"https://example.com/{i}" for i in range(20)]
    result = assistant(env).ask("Redis")
    assert not result.sufficient
    assert len([x for x in env.collectors.calls if x[0] == "collect"]) == 10


def test_redis_error_warnings_are_not_lost_when_next_request_starts(env):
    bot = assistant(env)
    bot.cache = SimpleNamespace(
        get=lambda *a: bot.warnings.append("Redis offline"),
        put=lambda *a: None,
    )
    for _ in range(2):
        result = bot.ask("Redis", local_only=True)
        assert "Redis offline" in result.warnings
