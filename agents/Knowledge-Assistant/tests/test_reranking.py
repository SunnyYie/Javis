from types import SimpleNamespace

import pytest
from conftest import bundle

from knowledge_assistant.agent import Assistant
from knowledge_assistant.cache import cache_key
from knowledge_assistant.models import ServiceError
from knowledge_assistant.reranking import Reranker
from knowledge_assistant.retrieval import select_evidence


def test_cross_encoder_scores_keep_input_order_and_reject_invalid_output(env):
    env.knowledge.ingest(bundle(paths={"a.md": "first", "b.md": "second"}))
    reranker = Reranker(env.s)
    seen = []

    def rank(query, documents, batch_size):
        seen.extend(documents)
        return [0.1, 0.9]

    reranker._model = SimpleNamespace(rerank=rank)
    chunks = env.store.active_chunks()
    assert reranker.scores("query", chunks) == [0.1, 0.9]
    assert all(c.text in text and c.path in text for c, text in zip(chunks, seen, strict=True))
    for values in ([1.0], [float("nan"), 1.0], [float("inf"), 1.0], [None, "invalid"]):
        reranker._model.rerank = lambda *a, **kw: values
        with pytest.raises(ServiceError, match="invalid_scores"):
            reranker.scores("query", chunks)


def test_reranker_window_limits_are_reported_without_truncating_originals(env):
    env.knowledge.ingest(bundle())
    chunks = env.store.active_chunks()
    tokenizer = env.embeddings.tokenizer
    tokenizer.enable_truncation(16)
    reranker = Reranker(env.s)
    reranker._model = SimpleNamespace(
        model=SimpleNamespace(tokenizer=tokenizer),
        rerank=lambda *a, **kw: [1.0] * len(chunks),
    )
    original = chunks[0].text
    assert reranker.scores("query", chunks) == [1.0]
    assert reranker.windowing == {"pair_token_limit": 16, "windowed_ids": [chunks[0].id]}
    assert tokenizer.truncation["max_length"] == 16
    assert chunks[0].text == original


def test_hybrid_candidate_pool_is_reranked_before_final_top_eight(env):
    env.knowledge.ingest(bundle(paths={f"f{i}.md": f"document {i}" for i in range(30)}))
    env.s.rerank_enabled = True
    env.s.hybrid_head = 0
    seen = []

    class ReverseRank:
        def scores(self, query, chunks):
            seen.extend(chunks)
            return list(range(len(chunks)))

    env.knowledge.reranker = ReverseRank()
    chunks = env.knowledge.retrieve("document")
    assert len(seen) > 8 and len(chunks) == 8
    assert [c.id for c in chunks] == [c.id for c in reversed(seen)][:8]
    trace = env.knowledge.last_trace[0]
    assert trace["selected"] == [c.id for c in chunks]
    assert len(trace["dense"]) <= 20 and len(trace["bm25"]) <= 20
    assert trace["rrf"] == [c.id for c in seen]
    assert trace["scores"][seen[-1].id] == len(seen) - 1


def test_recall_and_rerank_preserve_full_standalone_question(env):
    env.knowledge.ingest(bundle(paths={"README.md": "Introduction", "doc.md": "overview"}))
    env.s.rerank_enabled = True
    queries = []
    env.knowledge.reranker = SimpleNamespace(
        scores=lambda q, cs: queries.append(q) or [1.0] * len(cs)
    )
    question = "介绍example/project的定位、部署方式与语言要求"
    env.knowledge.retrieve(question)
    assert queries == [question]
    assert env.knowledge.last_trace[0]["query"] == question


def test_default_candidate_pool_preserves_both_recall_tails(env, monkeypatch):
    import rank_bm25

    env.knowledge.ingest(bundle(paths={f"file{i}.md": f"document {i}" for i in range(40)}))
    chunks = env.store.active_chunks()
    target = chunks[-1].id
    monkeypatch.setattr(env.index, "search", lambda *a: [c.id for c in chunks[:20]])
    monkeypatch.setattr(
        rank_bm25,
        "BM25Okapi",
        lambda corpus: SimpleNamespace(get_scores=lambda _: [0] * 20 + list(range(20, 0, -1))),
    )
    env.s.rerank_enabled = True
    env.knowledge.reranker = SimpleNamespace(
        scores=lambda q, cs: [float(c.id == target) for c in cs]
    )
    result = env.knowledge.retrieve("document")
    trace = env.knowledge.last_trace[0]
    assert env.s.rerank_candidates == 40
    assert len(trace["dense"]) == len(trace["bm25"]) == 20
    assert len(trace["rrf"]) == 40
    assert result[0].id == target


def test_reranker_failure_is_explicit_not_silent_hybrid_fallback(env):
    env.knowledge.ingest(bundle())
    env.s.rerank_enabled = True
    env.knowledge.reranker = SimpleNamespace(
        scores=lambda *a: (_ for _ in ()).throw(ServiceError("reranker", "failed"))
    )
    with pytest.raises(ServiceError, match="reranker: failed"):
        env.knowledge.retrieve("question")


def test_cache_isolated_by_retrieval_profile(env):
    original = cache_key("q", [], env.s, 1)
    env.s.rerank_enabled = True
    assert cache_key("q", [], env.s, 1) != original
    original = cache_key("q", [], env.s, 1)
    env.s.hybrid_head = 0
    assert cache_key("q", [], env.s, 1) != original
    original = cache_key("q", [], env.s, 1)
    env.s.reranker_model = "different"
    assert cache_key("q", [], env.s, 1) != original
    original = cache_key("q", [], env.s, 1)
    env.s.rerank_candidates = 16
    assert cache_key("q", [], env.s, 1) != original
    original = cache_key("q", [], env.s, 1)
    env.s.readme_policy = "all"
    assert cache_key("q", [], env.s, 1) != original


def test_cache_hit_skips_reranker_as_well_as_vector_and_llm(env):
    env.knowledge.ingest(bundle())
    env.s.rerank_enabled = True
    calls = []
    env.knowledge.reranker = SimpleNamespace(
        scores=lambda q, cs: calls.append(q) or [1.0] * len(cs)
    )
    assistant = Assistant(
        env.s,
        store=env.store,
        knowledge=env.knowledge,
        models=env.models,
        collectors=env.collectors,
        cache=env.cache,
    )
    assert assistant.ask("question").route == "local"
    before = (len(calls), env.index.searches, len(env.models.calls))
    assert assistant.ask("question").route == "cache"
    assert (len(calls), env.index.searches, len(env.models.calls)) == before
    assert env.collectors.calls == []


def test_recall_heads_survive_cross_encoder_with_fixed_total_budget(env):
    env.knowledge.ingest(bundle(paths={f"file{i}.md": f"text {i}" for i in range(16)}))
    ranking = env.store.active_chunks()
    lexical = [ranking[11].id, ranking[12].id]
    dense = [ranking[0].id, ranking[13].id]
    selected, protected = select_evidence(ranking, dense, lexical, 8)
    assert len(selected) == 8
    assert ranking[11] in selected
    assert ranking[8] not in selected
    assert protected == [ranking[11].id, ranking[0].id]
    assert selected == [c for c in ranking if c in selected]
    pure, protected = select_evidence(ranking, dense, lexical, 8, head=0)
    assert pure == ranking[:8] and not protected
    small, protected = select_evidence(ranking, dense, lexical, 1)
    assert small == [ranking[11]] and protected == lexical[:1]
    both, protected = select_evidence(ranking, dense, dense, 8)
    assert len(both) == 8 and protected == dense[:1]
    empty, protected = select_evidence([], dense, lexical, 8)
    assert not empty and not protected
