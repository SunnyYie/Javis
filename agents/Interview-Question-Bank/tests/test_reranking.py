from types import SimpleNamespace

import pytest
from conftest import bundle

from interview_question_bank.cache import request_id
from interview_question_bank.models import ServiceError
from interview_question_bank.retrieval import Reranker


def prepare(env):
    env.knowledge.ingest(bundle(paths={
        "a.md": "Unrelated architecture introduction and navigation.",
        "b.md": "BM25 RRF Milvus hybrid retrieval relevant answer.",
        "c.md": "Tools run with permissions and a strict timeout.",
    }))
    env.s.reranker_enabled = True
    return {c.path: c for c in env.store.active_chunks()}


def test_hybrid_pool_keeps_lexical_only_hit_before_rerank_and_top_k(env, monkeypatch):
    docs = prepare(env)
    monkeypatch.setattr(env.index, "search", lambda *args: [docs["a.md"].id])
    seen = []

    def scores(query, candidates):
        seen.extend(candidates)
        assert query == "原始中文问题"
        return [9.0 if c.path == "b.md" else -2.0 for c in candidates]

    env.knowledge.reranker = SimpleNamespace(scores=scores)
    result = env.knowledge.retrieve("BM25 RRF", k=1, rerank_query="原始中文问题")
    assert result == [docs["b.md"]]
    assert {c.path for c in seen} == {"a.md", "b.md"}
    trace = env.knowledge.last_trace
    assert trace["dense_ids"] == [docs["a.md"].id]
    assert docs["b.md"].id in trace["lexical_ids"]
    assert trace["final_ids"] == [docs["b.md"].id]
    assert len(trace["scores"]) == 2


def test_rerank_ties_keep_fused_order_and_disabled_mode_never_scores(env):
    prepare(env)
    env.knowledge.reranker = SimpleNamespace(scores=lambda query, chunks: [1.0] * len(chunks))
    first = env.knowledge.retrieve("BM25", k=3)
    assert [c.id for c in first] == env.knowledge.last_trace["fused_ids"]
    env.s.reranker_enabled = False
    env.knowledge.reranker = SimpleNamespace(scores=lambda *args: pytest.fail("disabled"))
    assert env.knowledge.retrieve("BM25", k=3) == first
    assert env.knowledge.last_trace["scores"] == []


@pytest.mark.parametrize("values", [[], [float("nan")], [float("inf")]])
def test_invalid_reranker_output_is_an_error_not_a_silent_fallback(env, values):
    prepare(env)
    env.knowledge.reranker = SimpleNamespace(scores=lambda *args: values)
    with pytest.raises(ServiceError, match="invalid_scores"):
        env.knowledge.retrieve("BM25", k=1)


def test_cross_encoder_model_failure_is_sanitized(env):
    reranker = Reranker(env.s)
    reranker._model = SimpleNamespace(rerank=lambda *args, **kwargs: (_ for _ in ()).throw(
        RuntimeError("provider-secret-must-not-be-exposed")
    ))
    with pytest.raises(ServiceError, match="reranker: model_or_scoring_failed") as exc:
        reranker.scores("question", [])
    assert "provider-secret" not in str(exc.value)


def test_rerank_configuration_invalidates_hot_answer_identity(env):
    key = request_id("RAG", [], env.s, "corpus")
    for field, value in (
        ("reranker_enabled", True),
        ("reranker_model", "different"),
        ("retrieval_candidates", 64),
    ):
        changed = env.s.model_copy(update={field: value})
        assert request_id("RAG", [], changed, "corpus") != key


def test_no_candidates_does_not_load_cross_encoder(env):
    env.s.reranker_enabled = True
    env.knowledge.reranker = SimpleNamespace(scores=lambda *args: pytest.fail("empty corpus"))
    assert env.knowledge.retrieve("RAG") == []
    assert env.knowledge.last_trace["final_ids"] == []
