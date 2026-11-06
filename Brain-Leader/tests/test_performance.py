import importlib
import json
import sqlite3

import pytest

from perf.benchmark import settings_for, source_state
from perf.compare import compare
from perf.metrics import summarize


def test_load_metrics_keep_failures_and_use_nearest_rank():
    result = summarize([(1, False), (2, False), (100, False), (5000, True)], 2)
    assert result["completed_qps"] == 2
    assert result["successful_qps"] == 1.5
    assert result["failures"] == 1
    assert result["p50_ms"] == 2
    assert result["p99_ms"] == 100
    assert result["p99_low_sample_warning"]


def test_empty_load_has_no_fabricated_percentiles():
    result = summarize([], 1)
    assert result["successful_qps"] == 0
    assert result["p99_ms"] is None
    with pytest.raises(ValueError):
        summarize([], 0)


def test_benchmark_refuses_production_namespace(tmp_path):
    manifest = {
        "knowledge": {
            "namespace": "ka", "data_dir": str(tmp_path),
            "source": str(tmp_path / "knowledge.sqlite3"),
        },
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="production namespace"):
        settings_for(tmp_path)


def test_production_metadata_read_is_read_only(tmp_path):
    missing = tmp_path / "missing.sqlite3"
    with pytest.raises(Exception):
        source_state({"questions": {"source": str(missing)}})
    assert not missing.exists()


def test_comparison_keeps_quality_mismatches_and_failures(tmp_path):
    from perf.benchmark import write

    state = {"knowledge": {"version": "1"}, "questions": {"version": "2"}}
    write(tmp_path / "production-before.json", state)
    for kind in ("knowledge", "questions"):
        for phase in ("baseline", "optimized"):
            directory = tmp_path / phase / kind
            write(directory / "profile-results.json", {
                "retrieval": {"topic": {"ids": ["same"]}}, "snapshots": [{"count": 1}],
            })
            write(tmp_path / phase / f"{kind}-direct-profile/results.json", {
                "retrieval_ids": {"topic": ["same"]},
            })
            write(directory / "production-after.json", state)
            write(directory / "retrieve-u1.json", summarize([(1, False)], 1))
    assert compare(tmp_path)["all_checks_passed"]
    write(tmp_path / "optimized/questions/profile-results.json", {
        "retrieval": {"topic": {"ids": ["changed"]}}, "snapshots": [{"count": 1}],
    })
    write(tmp_path / "optimized/questions/retrieve-u1.json", summarize([(1, True)], 1))
    result = compare(tmp_path)
    assert not result["all_checks_passed"]
    assert not result["checks"]["questions_five_topic_ids_and_order"]
    assert result["request_failures"] == 1


@pytest.mark.parametrize("package", ["knowledge_assistant", "interview_question_bank"])
def test_active_snapshot_cache_is_fresh_and_does_not_leak_mutation(tmp_path, package):
    store = importlib.import_module(f"{package}.storage").Store(tmp_path)
    payload = {
        "id": "fixture", "source": "https://example.invalid/source", "text": "original",
        "url": "https://example.invalid/source", "path": "sample.md", "start": 0, "end": 1,
        "fetched_at": 1., "expires_at": 2.,
    }
    try:
        with store.db:
            store.db.execute("INSERT INTO chunks VALUES(?,?,?)", (
                "fixture", json.dumps(payload), "[]",
            ))
            store.db.execute("INSERT INTO active VALUES(?,?)", (payload["source"], "fixture"))
        first = store.active_chunks()
        first[0].text = "caller mutation"
        reads = []
        store.db.set_trace_callback(reads.append)
        assert store.active_chunks()[0].text == "original"
        assert not any("SELECT payload FROM chunks" in sql for sql in reads)
        store.db.set_trace_callback(None)
        version = store.version
        # Same-version metadata refreshes must not leave cached evidence stale.
        payload["expires_at"] = 9.
        with store.db:
            store.db.execute("UPDATE chunks SET payload=?", (json.dumps(payload),))
        assert store.active_chunks()[0].expires_at == 9.
        with sqlite3.connect(tmp_path / "knowledge.sqlite3") as external:
            payload["text"] = "external revision"
            external.execute("UPDATE chunks SET payload=?", (json.dumps(payload),))
        assert store.active_chunks()[0].text == "external revision"
        assert store.version == version
        payload["text"] = "uncommitted"
        store.db.execute("UPDATE chunks SET payload=?", (json.dumps(payload),))
        assert store.active_chunks()[0].text == "uncommitted"
        store.db.rollback()
        assert store.active_chunks()[0].text == "external revision"
    finally:
        store.db.close()


@pytest.mark.parametrize("package", ["knowledge_assistant", "interview_question_bank"])
def test_exact_rerank_cache_is_bounded_and_keeps_order(tmp_path, package):
    from types import SimpleNamespace

    config = importlib.import_module(f"{package}.config").Settings
    module = "reranking" if package == "knowledge_assistant" else "retrieval"
    reranker_type = importlib.import_module(f"{package}.{module}").Reranker
    settings = config(_env_file=None, data_dir=tmp_path, inference_cache_size=2)
    calls = []

    class Encoder:
        def rerank(self, query, texts, batch_size):
            calls.append((query, texts, batch_size))
            return [float(i) for i in range(len(texts))]

    reranker = reranker_type(settings)
    reranker._model = Encoder()
    chunks = [
        SimpleNamespace(text=text, repo="", source="https://example.invalid", path="", id=text)
        for text in ("first", "second")
    ]
    scores = reranker.scores("query", chunks)
    scores[0] = 99
    assert reranker.scores("query", chunks) == [0., 1.]
    assert len(calls) == 1 and reranker.last_cache_hit
    reranker.scores("query", chunks[::-1])
    assert len(calls) == 2
    reranker.scores("other", chunks)
    assert len(reranker._score_cache) == 2
    reranker.scores("query", chunks)
    assert len(calls) == 4
    settings.inference_cache_size = 0
    reranker.scores("query", chunks)
    assert len(calls) == 5 and not reranker.last_cache_hit


@pytest.mark.parametrize("package", ["knowledge_assistant", "interview_question_bank"])
def test_rerank_cache_invalidates_replaced_model_and_callable(tmp_path, package):
    from types import SimpleNamespace

    config = importlib.import_module(f"{package}.config").Settings
    module = "reranking" if package == "knowledge_assistant" else "retrieval"
    reranker_type = importlib.import_module(f"{package}.{module}").Reranker
    reranker = reranker_type(config(_env_file=None, data_dir=tmp_path))
    chunks = [SimpleNamespace(text="text", repo="", source="source", path="", id="one")]
    reranker._model = SimpleNamespace(rerank=lambda *a, **kw: [1.])
    assert reranker.scores("query", chunks) == [1.]
    reranker._model.rerank = lambda *a, **kw: [2.]
    assert reranker.scores("query", chunks) == [2.]
    reranker._model = SimpleNamespace(rerank=lambda *a, **kw: [3.])
    assert reranker.scores("query", chunks) == [3.]
    assert len(reranker._score_cache) == 1
    reranker._model.rerank = lambda *a, **kw: [float("nan")]
    for _ in range(2):
        with pytest.raises(Exception):
            reranker.scores("invalid", chunks)
    assert len(reranker._score_cache) == 1


def test_token_counter_is_reused_without_mutating_inference_tokenizer(tmp_path):
    from types import SimpleNamespace

    from knowledge_assistant.config import Settings
    from knowledge_assistant.reranking import Reranker
    from tokenizers import Tokenizer, models, pre_tokenizers

    tokenizer = Tokenizer(models.WordLevel({"[UNK]": 0, "word": 1}, unk_token="[UNK]"))
    tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
    tokenizer.enable_truncation(max_length=4)
    tokenizer.enable_padding(length=4)
    reranker = Reranker(Settings(_env_file=None, data_dir=tmp_path))
    reranker._model = SimpleNamespace(
        model=SimpleNamespace(tokenizer=tokenizer), rerank=lambda *a, **kw: [1.],
    )
    chunk = SimpleNamespace(
        text="word " * 10, repo="", source="source", path="", id="one",
    )
    before = tokenizer.to_str()
    reranker.scores("word", [chunk])
    counter = reranker._counter
    chunk.id = "changed-id"
    reranker.scores("word", [chunk])
    assert reranker.last_cache_hit
    assert reranker._counter is counter
    assert reranker.windowing["windowed_ids"] == ["changed-id"]
    assert tokenizer.to_str() == before


def test_adapter_optional_performance_overrides_do_not_change_defaults(tmp_path):
    from javis_tools.adapters import Assistants

    from brain_leader.config import Settings

    settings = Settings(_env_file=None, workspace=tmp_path, llm_env_file=tmp_path / "missing")
    adapters = Assistants(settings, "test")
    try:
        assert adapters.knowledge_settings("knowledge").reranker_batch_size == 4
        assert adapters.knowledge_settings("questions").reranker_batch_size == 8
        settings.embedding_batch_size = 8
        settings.reranker_batch_size = 2
        settings.inference_cache_size = 0
        for kind in ("knowledge", "questions"):
            child = adapters.knowledge_settings(kind)
            assert child.embedding_batch_size == 8
            assert child.reranker_batch_size == 2
            assert child.inference_cache_size == 0
    finally:
        adapters.close()


@pytest.mark.parametrize("package", ["knowledge_assistant", "interview_question_bank"])
def test_answer_cache_identity_includes_reranker_batch_size(tmp_path, package):
    settings = importlib.import_module(f"{package}.config").Settings(
        _env_file=None, data_dir=tmp_path,
    )
    cache = importlib.import_module(f"{package}.cache")

    def key():
        if package == "knowledge_assistant":
            return cache.cache_key("query", [], settings, 1, "isolated")
        return cache.request_id("query", [], settings, "isolated")

    original = key()
    settings.reranker_batch_size = 2
    assert key() != original


@pytest.mark.parametrize("package", ["knowledge_assistant", "interview_question_bank"])
def test_embedding_query_cache_is_bounded_and_keeps_validation(tmp_path, package):
    from types import SimpleNamespace

    config = importlib.import_module(f"{package}.config").Settings
    embeddings_type = importlib.import_module(f"{package}.retrieval").Embeddings
    settings = config(_env_file=None, data_dir=tmp_path, inference_cache_size=2)
    calls = []

    class Model:
        def query_embed(self, text):
            calls.append(text)
            yield SimpleNamespace(tolist=lambda: [1., 2.])

        def embed(self, texts, batch_size):
            calls.append(batch_size)
            for _ in texts:
                yield SimpleNamespace(tolist=lambda: [1., 2.])

    embeddings = embeddings_type(settings)
    embeddings._model = Model()
    embeddings._tokenizer = SimpleNamespace(encode=lambda text: SimpleNamespace(ids=list(text)))
    first = embeddings.query("one")
    first[0] = 99
    assert embeddings.query("one") == [1., 2.]
    assert calls == ["one"] and embeddings.last_cache_hit
    for query in ("two", "three", "one"):
        embeddings.query(query)
    assert len(embeddings._query_cache) == 2 and len(calls) == 4
    with pytest.raises(Exception):
        embeddings.query("x" * 513)
    assert len(calls) == 4
    embeddings.documents(["text"])
    assert calls[-1] == 16
