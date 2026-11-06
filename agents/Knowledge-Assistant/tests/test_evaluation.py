import asyncio
import json
from types import SimpleNamespace

import pytest
from conftest import bundle

from knowledge_assistant.agent import Assistant
from knowledge_assistant.evaluation import (
    METRICS,
    EvaluationCase,
    error_record,
    generate_record,
    load_cases,
    score_record,
    summarize,
    write_json,
)


def test_evaluation_keeps_all_generation_contexts_not_only_citations(env):
    env.knowledge.ingest(bundle(paths={"a.md": "first document", "b.md": "second document"}))
    case = EvaluationCase(
        id="one",
        question="only this question",
        reference="SECRET REFERENCE MUST NOT LEAK",
        evidence=[{"source": bundle().source, "contains": "first"}],
    )
    seen = []
    original = env.models.rewrite
    env.models.rewrite = lambda q, history: seen.append(q) or original(q, history)
    assistant = Assistant(
        env.s,
        store=env.store,
        knowledge=env.knowledge,
        models=env.models,
        collectors=env.collectors,
        cache=env.cache,
    )
    row = generate_record(case, assistant, "hybrid")
    assert seen == [case.question]
    assert len(row["retrieved_contexts"]) == 2
    assert len(row["answer"]["citations"]) == 1
    assert row["reference"] == case.reference and row["support_recall"] == 1
    assert env.collectors.calls == []
    assert row["answer"]["retrieval"][0]["reranker"] is None


def test_invalid_cases_are_rejected(tmp_path):
    path = tmp_path / "cases.json"
    path.write_text("[]")
    with pytest.raises(ValueError):
        load_cases(path)
    row = {
        "id": "same",
        "question": "q",
        "reference": "r",
        "evidence": [{"source": "https://example.com", "contains": "supported"}],
    }
    path.write_text(json.dumps([row, row]))
    with pytest.raises(ValueError, match="unique"):
        load_cases(path)


def test_failed_or_nan_metrics_remain_missing_not_zero_or_success(tmp_path):
    class Good:
        async def ascore(self, user_input):
            return SimpleNamespace(value=0.5)

    class Bad:
        async def ascore(self, response):
            raise ConnectionError("do-not-log-secret")

    class Nan:
        async def ascore(self, reference):
            return SimpleNamespace(value=float("nan"))

    metrics = dict(zip(METRICS, [Good(), Bad(), Nan(), Good()], strict=True))
    row = {"id": "a", "profile": "hybrid", "user_input": "q", "response": "a", "reference": "r"}
    scored = asyncio.run(score_record(row, metrics))
    report = summarize([row], [scored])
    assert not report["complete"]
    assert scored["metrics"]["answer_relevancy"]["error"] == {"category": "ConnectionError"}
    assert scored["metrics"]["faithfulness"]["score"] == 0.5
    assert scored["metrics"]["context_precision"]["score"] is None
    assert report["profiles"]["hybrid"]["metrics"]["context_precision"] == {
        "mean": None,
        "valid": 0,
        "expected": 1,
        "failed": 1,
    }
    path = tmp_path / "report.json"
    write_json(path, report)
    assert "NaN" not in path.read_text() and "do-not-log-secret" not in json.dumps(scored)


def test_evaluation_timeouts_and_generation_failure_are_not_passed():
    class Slow:
        async def ascore(self, user_input):
            await asyncio.sleep(1)

    row = {"id": "one", "profile": "hybrid", "user_input": "q"}
    result = asyncio.run(score_record(row, {"faithfulness": Slow()}, timeout=0.001))
    assert result["metrics"]["faithfulness"]["score"] is None
    assert result["metrics"]["faithfulness"]["error"]["category"] == "TimeoutError"
    row["generation_error"] = {"category": "unavailable"}
    result = asyncio.run(score_record(row, {"faithfulness": Slow()}))
    assert result["metrics"]["faithfulness"]["error"]["category"] == "generation_failed"
    assert not summarize([], [])["complete"]


def test_judge_truncation_diagnostics_never_include_completion_body():
    exc = RuntimeError("secret error")
    exc.last_completion = SimpleNamespace(
        choices=[SimpleNamespace(finish_reason="length", message="secret body")],
        usage=SimpleNamespace(prompt_tokens=100, completion_tokens=4096, total_tokens=4196),
    )
    assert error_record(exc) == {
        "category": "RuntimeError",
        "finish_reason": "length",
        "prompt_tokens": 100,
        "completion_tokens": 4096,
        "total_tokens": 4196,
    }
