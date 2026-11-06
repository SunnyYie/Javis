import json
import time

import pytest
from conftest import bundle

from interview_question_bank.chunking import Chunker, symbol_ranges
from interview_question_bank.models import Document, ServiceError
from interview_question_bank.retrieval import fuse, terms


def test_publish_and_duplicate_are_idempotent(env):
    first = env.knowledge.ingest(bundle())
    calls = len(env.embeddings.calls)
    second = env.knowledge.ingest(bundle())
    assert first["version"] == second["version"] == 1
    assert second["embedded"] == 0
    assert len(env.embeddings.calls) == calls
    assert len(env.store.active_chunks()) == 1
    coverage = json.loads(env.store.sources()[0]["coverage"])
    assert (env.s.data_dir / "snapshots" / (coverage["snapshot"] + ".json")).exists()


def test_update_only_embeds_changed_content_and_deletes_removed_files(env):
    env.knowledge.ingest(bundle(paths={"README.md": "stable", "src/a.py": "x = 1\n"}))
    env.embeddings.calls.clear()
    result = env.knowledge.ingest(
        bundle(
            commit="def",
            paths={
                "README.md": "stable",
                "src/b.py": "x = 2\n",
            },
        )
    )
    assert env.embeddings.calls == ["x = 2\n"]
    assert result["version"] == 2
    chunks = env.store.active_chunks()
    assert {c.path for c in chunks} == {"README.md", "src/b.py"}
    assert all(c.commit == "def" for c in chunks)
    assert all("/def/" in c.url for c in chunks)


def test_failed_vector_write_preserves_active_version(env):
    env.knowledge.ingest(bundle())
    before = [c.id for c in env.store.active_chunks()]
    env.index.write_failure = True
    with pytest.raises(ServiceError):
        env.knowledge.ingest(bundle("replacement", commit="def"))
    assert env.store.version == 1
    assert [c.id for c in env.store.active_chunks()] == before


def test_failed_sqlite_publish_does_not_activate_orphan_vectors(env, monkeypatch):
    env.knowledge.ingest(bundle())
    before = [c.id for c in env.store.active_chunks()]
    monkeypatch.setattr(env.store, "publish", lambda *a: (_ for _ in ()).throw(OSError()))
    with pytest.raises(OSError):
        env.knowledge.ingest(bundle("replacement", commit="def"))
    assert len(env.index.rows) == 2
    assert [c.id for c in env.knowledge.retrieve("execution")] == before


def test_embedding_change_is_rejected(env):
    env.knowledge.ingest(bundle())
    env.embeddings.fingerprint = "different:8"
    with pytest.raises(RuntimeError, match="Embedding changed"):
        env.knowledge.ingest(bundle())
    with pytest.raises(RuntimeError, match="Embedding changed"):
        env.knowledge.retrieve("anything")


def test_data_directory_cannot_silently_switch_vector_namespace(env):
    from interview_question_bank.retrieval import Knowledge

    env.s.index_namespace = "different"
    with pytest.raises(RuntimeError, match="Index namespace changed"):
        Knowledge(env.s, env.store, env.embeddings, env.index)


def test_incomplete_snapshot_rejected(env):
    b = bundle()
    b.complete = False
    with pytest.raises(ServiceError, match="incomplete_snapshot"):
        env.knowledge.ingest(b)
    assert env.store.version == 0


def test_empty_index_still_checks_milvus(env):
    env.index.available = False
    with pytest.raises(ServiceError):
        env.knowledge.retrieve("LangGraph")


def test_multilingual_terms_and_rrf():
    tokens = terms("如何调用 get_llm LangGraph 的工具？")
    assert "get_llm" in tokens and "langgraph" in tokens
    assert any("工具" in t for t in tokens)
    assert fuse(["a", "b"], ["b", "c"])[0] == "b"


def test_markdown_respects_headings_and_fences():
    text = "# A\nintro\n```\n# not a heading\n```\n## B\nbody\n"
    assert symbol_ranges(text, "README.md") == [(0, 5), (5, 7)]


def test_python_symbol_boundaries():
    text = "import os\n\ndef a():\n    return 1\n\nclass B:\n    pass\n"
    ranges = symbol_ranges(text, "x.py")
    assert (2, 4) in ranges and (5, 7) in ranges


@pytest.mark.parametrize("text", ["abc " * 400, "中文符号测试" * 400, "long_line" * 300])
def test_chunks_are_bounded_exact_and_cover_all_nonwhitespace(env, text):
    chunker = Chunker(env.embeddings.tokenizer, "fake", tokens=100, overlap=10)
    doc = Document("https://example.com/x", "https://example.com/x", text, path="body")
    chunks = chunker.split(doc, 100)
    assert len(chunks) > 1
    assert all(chunker.count(c.text) <= 100 for c in chunks)
    assert all(c.text in text for c in chunks)
    # Adjacent windows overlap; the very last source character is not lost.
    assert text.rstrip().endswith(chunks[-1].text.rstrip())


def test_sessions_persist_and_history_is_bounded(env):
    sid = env.store.new_session()
    for i in range(10):
        env.store.add_turn(sid, str(i), "answer")
    history = env.store.history(sid)
    assert [t["question"] for t in history] == [str(i) for i in range(4, 10)]
    assert sid in env.store.sessions()


def test_freshness_is_renewed_on_unchanged_import(env):
    old = time.time() - 100
    env.knowledge.ingest(bundle(fetched_at=old))
    previous = env.store.active_chunks()[0].expires_at
    env.knowledge.ingest(bundle())
    assert env.store.active_chunks()[0].expires_at > previous
    assert env.store.version == 1


def test_source_role_change_invalidates_answer_version(env):
    original = bundle()
    env.knowledge.ingest(original)
    original.documents[0].role = "third_party"
    env.knowledge.ingest(original)
    assert env.store.version == 2


def test_bm25_is_reused_only_for_same_corpus_version(env):
    env.knowledge.ingest(bundle())
    env.knowledge.retrieve("LangGraph")
    index = env.knowledge._bm25
    env.knowledge.retrieve("durable execution")
    assert env.knowledge._bm25 is index
    env.knowledge.ingest(bundle("different content", commit="def"))
    env.knowledge.retrieve("different")
    assert env.knowledge._bm25 is not index
