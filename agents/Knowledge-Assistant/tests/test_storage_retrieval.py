import json
import time

import pytest
from conftest import bundle

from knowledge_assistant.chunking import Chunker, symbol_ranges
from knowledge_assistant.models import Document, ServiceError
from knowledge_assistant.retrieval import fuse, is_readme, terms


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


def test_explicit_repository_scope_balances_comparison_and_recall_budgets(env, monkeypatch):
    for repo in ("example/alpha", "example/beta", "other/unrelated"):
        b = bundle(
            paths={f"doc{i}.md": f"Introduction overview {repo} document {i}" for i in range(12)},
            source=f"https://github.com/{repo}",
        )
        b.repo = repo
        for doc in b.documents:
            doc.repo = repo
        env.knowledge.ingest(b)
    calls = []
    original = env.index.search

    def search(vector, active, k):
        calls.append((active, k))
        return original(vector, active, k)

    monkeypatch.setattr(env.index, "search", search)
    result = env.knowledge.retrieve("EXAMPLE/ALPHA 和 example/beta 分别是什么？对比定位")
    assert len(result) == 8
    assert [c.repo for c in result] == ["example/alpha", "example/beta"] * 4
    assert [k for _, k in calls] == [10, 10]
    assert all(len(ids) == 12 for ids, _ in calls)
    # A scoped lexical index must not contaminate a subsequent whole-library query.
    env.knowledge.retrieve("unrelated")
    assert len(env.knowledge._lexical_ids) == 36


def test_repository_scope_does_not_match_partial_name(env):
    env.knowledge.ingest(bundle())
    env.knowledge.retrieve("example/project-extended")
    assert env.knowledge._lexical_version[:2] == (env.store.version, "")


def test_explicit_readme_scope_excludes_templates_and_preserves_detailed_query(env, monkeypatch):
    env.knowledge.ingest(
        bundle(paths={"README.md": "project overview", "templates/AGENTS.md": "project overview"})
    )
    queries = []
    monkeypatch.setattr(env.embeddings, "query", lambda q: queries.append(q) or [1, 1, 0.5])
    query = "example/project README 是什么，如何实现调用？"
    result = env.knowledge.retrieve(query)
    assert {c.path for c in result} == {"README.md"}
    assert queries == [query]
    assert len(env.knowledge.retrieve("example/project overview")) == 2


def test_canonical_readme_selection_keeps_snapshots_and_explicit_language(env):
    env.knowledge.ingest(
        bundle(
            paths={
                "README.md": "main introduction deployment",
                "README_zh.md": "中文介绍",
                "README_ja.md": "日本語",
                "README.i18n.yaml": "translation metadata",
                "docs/README_fr.md": "unique document without a canonical sibling",
                "src/core.py": "x = 1\n",
            }
        )
    )
    result = env.knowledge.retrieve("example/project README")
    assert {c.path for c in result} == {"README.md"}
    canonical_index = env.knowledge._bm25
    result = env.knowledge.retrieve("example/project README_zh.md")
    assert {c.path for c in result} == {"README_zh.md"}
    assert env.knowledge._bm25 is not canonical_index
    result = env.knowledge.retrieve("example/project README.md 与 README_ja.md")
    assert {c.path for c in result} == {"README.md", "README_ja.md"}
    result = env.knowledge.retrieve("example/project documents")
    assert {c.path for c in result} == {
        "README.md",
        "README.i18n.yaml",
        "docs/README_fr.md",
        "src/core.py",
    }
    # Retrieval filtering does not delete translated knowledge or change the version.
    assert len(env.store.active_chunks()) == 6 and env.store.version == 1
    assert not is_readme("README.i18n.yaml")


def test_readme_without_canonical_and_all_policy_remain_accessible(env):
    env.knowledge.ingest(bundle(paths={"README_zh.md": "中文", "README_fr.md": "français"}))
    assert len(env.knowledge.retrieve("example/project README")) == 2
    env.knowledge.ingest(bundle(paths={"README.md": "main", "README_zh.md": "中文"}))
    env.s.readme_policy = "all"
    assert len(env.knowledge.retrieve("example/project README")) == 2


def test_explicit_configuration_file_is_not_mistaken_for_readme(env):
    env.knowledge.ingest(bundle(paths={"README.md": "docs", "README.i18n.yaml": "config"}))
    result = env.knowledge.retrieve("example/project README.i18n.yaml")
    assert {c.path for c in result} == {"README.i18n.yaml"}
