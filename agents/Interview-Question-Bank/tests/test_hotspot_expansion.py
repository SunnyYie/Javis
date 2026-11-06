import importlib.util
import json
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_research_connection_cannot_modify_sibling_database(tmp_path):
    module = load_script("research_agent_hotspots")
    path = tmp_path / "source.sqlite3"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE test(value TEXT)")
    db.commit()
    db.close()
    readonly = module.connect_readonly(path)
    with pytest.raises(sqlite3.OperationalError):
        readonly.execute("INSERT INTO test VALUES ('must not write')")
    readonly.close()


def test_expansion_selects_topical_bodies_and_excludes_large_or_symlink_files():
    module = load_script("expand_agent_hotspots")
    entries = [
        dict(path="README.md", type="blob", size=50),
        dict(path="docs/events.mdx", type="blob", size=150),
        dict(path="docs/security.mdx", type="blob", size=150),
        dict(path="docs/huge.mdx", type="blob", size=300000),
        dict(path="docs/link.mdx", type="blob", size=50, mode="120000"),
        dict(path="node_modules/docs/events.mdx", type="blob", size=50),
    ]
    assert module.selected_paths(entries, r"^docs/.*\.mdx$", "events", 3) == [
        "README.md", "docs/events.mdx", "docs/security.mdx",
    ]


def test_scoped_retrieval_requires_selected_source_and_fresh_evidence():
    module = load_script("expand_agent_hotspots")
    chunks = [
        SimpleNamespace(id="yes", source="https://official", expires_at=time.time() + 100),
        SimpleNamespace(id="old", source="https://official", expires_at=0),
        SimpleNamespace(id="other", source="https://other", expires_at=time.time() + 100),
    ]
    store = SimpleNamespace(active_chunks=lambda: chunks, version=8)
    scoped = module.ScopedStore(store, ["https://official"])
    assert [c.id for c in scoped.active_chunks()] == ["yes"]
    assert scoped.version == 8


def test_hotspot_plan_has_no_global_target_and_keeps_original_plan_unchanged():
    plan = json.loads((ROOT / "config/agent_hotspots_20261106.json").read_text())
    original = json.loads((ROOT / "config/ai_agent_curriculum.json").read_text())
    assert "target" not in plan and "count" not in plan
    assert len({t["id"] for t in plan["topics"]}) == len(plan["topics"]) == 11
    assert all(t["facets"] and t["repos"] and t["hot"] for t in plan["topics"])
    assert not {t["id"] for t in plan["topics"]} & {t["id"] for t in original["topics"]}
    assert sum(t["count"] for t in original["topics"]) == 1000


def test_public_fallback_pins_commit_and_ignores_external_or_agent_instruction_links():
    module = load_script("expand_agent_hotspots")
    revision = "a" * 40
    route = {"payload": {"codeViewRepoRoute": {
        "refInfo": {"currentOid": revision},
        "tree": {"items": [{"path": "README.md", "contentType": "file"}]},
    }}}
    page = '<script data-target="react-app.embeddedData">' + json.dumps(route) + "</script>"
    raw = f"https://raw.githubusercontent.com/example/agent/{revision}/"
    replies = {
        "https://github.com/example/agent": page,
        raw + "README.md": (
            "# Agent\n[events](docs/events.md)\n"
            "[external](https://example.org/evil.md)\n[instructions](AGENTS.md)\n"
        ),
        raw + "docs/events.md": "# Events\nRaw factual documentation.",
    }
    calls = []

    class HTTP:
        def text(self, url, **kwargs):
            calls.append(url)
            return replies[url]

    bundle = module.public_bundle(
        HTTP(), SimpleNamespace(max_file_bytes=262144),
        dict(repo="example/agent", query="events", include=r"^docs/.*\.md$"),
    )
    assert bundle.commit == revision and bundle.coverage["partial"]
    assert [d.path for d in bundle.documents] == ["README.md", "docs/events.md"]
    assert all(revision in d.url for d in bundle.documents)
    assert not any("evil" in url or "AGENTS" in url for url in calls)


def test_public_fallback_rejects_invalid_commit_before_reading_raw_body():
    module = load_script("expand_agent_hotspots")
    route = {"payload": {"codeViewRepoRoute": {"refInfo": {"currentOid": "main"}}}}
    page = '<script data-target="react-app.embeddedData">' + json.dumps(route) + "</script>"
    http = SimpleNamespace(text=lambda *a, **kw: page)
    with pytest.raises(ValueError, match="Invalid public commit"):
        module.public_bundle(http, SimpleNamespace(max_file_bytes=262144),
                             dict(repo="example/agent", query="events", include=r".*"))


def test_transient_reference_cleanup_preserves_claims_and_durable_evidence():
    module = load_script("expand_agent_hotspots")
    question = SimpleNamespace(
        answer="Typed state is synchronized（E1、E3）. HTTP stays a transport.",
        key_points=["Keep approval at the client (E2, E4)", "Use the E1 error code."],
        evidence_ids=["durable-one", "durable-two"],
    )
    module.clean_references(question)
    assert question.answer == "Typed state is synchronized. HTTP stays a transport."
    assert question.key_points == ["Keep approval at the client ", "Use the E1 error code."]
    assert question.evidence_ids == ["durable-one", "durable-two"]
    question.key_points = ["Resume from a step（E3/E11）", "Keep state（对照 E3）"]
    module.clean_references(question)
    assert question.key_points == ["Resume from a step", "Keep state"]


def test_finalize_does_not_modify_baseline_or_uncatalogued_chat_records(env, tmp_path, monkeypatch):
    module = load_script("expand_agent_hotspots")
    bank = module.QuestionBank(
        env.s, store=env.store, embeddings=env.embeddings, index=env.index,
    )
    question = module.InterviewQuestion(
        title="A supported question", difficulty="基础", knowledge_points=["state"],
        answer="A factual claim（E1）", key_points=["A supported point（E1）"],
        origin="基于资料生成", evidence_ids=["raw"],
    )
    original = question.model_dump_json()
    with env.store.db:
        for qid in ("baseline", "added", "chat-only"):
            env.store.db.execute("INSERT INTO question_records VALUES(?,?,?,?,?)",
                                 (qid, original, "[]", 0, time.time()))
        for qid in ("baseline", "added"):
            env.store.db.execute("INSERT INTO question_catalog VALUES(?,?,?,?,?,?,?,?,?)",
                                 (qid, qid, "topic", "Topic", 1, "llm_verified", "[]",
                                  env.s.embedding_model, "plan"))
    (tmp_path / "research.json").write_text(json.dumps({
        "baseline": {"question_ids": ["baseline"]},
    }))
    monkeypatch.setattr(module, "QuestionBank", lambda _: bank)
    module.finalize(tmp_path)
    records = dict(env.store.db.execute("SELECT id,payload FROM question_records"))
    assert records["baseline"] == records["chat-only"] == original
    assert json.loads(records["added"])["answer"] == "A factual claim"
    assert json.loads((tmp_path / "finalization.json").read_text())["ids"] == ["added"]


def test_numpy_validation_checks_serialize_without_stringifying_results():
    module = load_script("expand_agent_hotspots")
    report = module.summarize_checks({"ok": np.bool_(True), "failed": np.bool_(False)})
    assert report["passed"] is False
    assert type(report["passed_count"]) is int and report["passed_count"] == 1
    assert type(report["failed_count"]) is int and report["failed_count"] == 1
    assert json.loads(json.dumps(report))["checks"] == {"ok": True, "failed": False}


def test_explicit_repository_paths_are_complete_bounded_and_pin_topical_bodies():
    module = load_script("expand_agent_hotspots")
    entries = [
        dict(path="README.md", type="blob", size=50),
        dict(path="docs/approval.md", type="blob", size=150),
        dict(path="docs/large.md", type="blob", size=300000),
        dict(path="docs/link.md", type="blob", size=50, mode="120000"),
    ]
    spec = dict(paths=["docs/approval.md"], max_documents=2)
    assert module.repository_paths(entries, spec, 262144) == ["README.md", "docs/approval.md"]
    with pytest.raises(ValueError, match="budget"):
        module.repository_paths(entries, dict(spec, max_documents=1), 262144)
    for path in ("docs/missing.md", "docs/large.md", "docs/link.md"):
        with pytest.raises(ValueError, match="missing"):
            module.repository_paths(entries, dict(spec, paths=[path]), 262144)
    with pytest.raises(ValueError, match="budget"):
        module.repository_paths(entries, dict(spec, max_documents=31), 262144)


def test_catalog_digest_detects_payload_citation_and_catalog_changes():
    module = load_script("expand_agent_hotspots")
    db = sqlite3.connect(":memory:")
    db.executescript("""
        CREATE TABLE question_records(id TEXT PRIMARY KEY,payload TEXT,citations TEXT);
        CREATE TABLE question_catalog(question_id TEXT PRIMARY KEY,topic TEXT,vector TEXT);
        INSERT INTO question_records VALUES('old','answer','evidence');
        INSERT INTO question_catalog VALUES('old','original','[0.1]');
    """)
    previous = module.catalog_digests(db)
    db.execute("UPDATE question_records SET citations='changed'")
    assert module.catalog_digests(db)["old"] != previous["old"]
    db.execute("UPDATE question_records SET citations='evidence'")
    assert module.catalog_digests(db) == previous
    db.execute("UPDATE question_catalog SET topic='other'")
    assert module.catalog_digests(db)["old"] != previous["old"]
    db.close()


def test_harness_gap_plan_keeps_required_repositories_and_has_no_global_target():
    plan = json.loads((ROOT / "config/harness_gaps_20261106.json").read_text())
    assert "target" not in plan and "count" not in plan
    assert {
        "microsoft/agent-framework", "crewAIInc/crewAI", "shareAI-lab/learn-claude-code",
    } <= {s["repo"] for s in plan["repositories"]}
    old = json.loads((ROOT / "config/agent_hotspots_20261106.json").read_text())
    original = json.loads((ROOT / "config/ai_agent_curriculum.json").read_text())
    assert not {t["id"] for t in plan["topics"]} & {
        t["id"] for p in (old, original) for t in p["topics"]
    }
    repos = {s["repo"] for s in plan["repositories"]}
    assert all(t["facets"] and set(t["repos"]) <= repos for t in plan["topics"])
    assert all(s["paths"] and len(s["paths"]) < s["max_documents"] <= 30
               for s in plan["repositories"])


def test_harness_coverage_uses_question_text_not_source_citations(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    module = load_script("research_harness_gaps")
    rows = [
        dict(id="one", title="CrewAI Flows", answer="State persistence"),
        dict(id="two", title="Existing generic compression", answer="Context compression",
             citations=[{"text": "Microsoft Agent Framework and CrewAI"}]),
    ]
    result = module.coverage(rows)
    assert result["CrewAI"]["title_count"] == 1
    assert result["Microsoft Agent Framework"]["title_count"] == 0
    assert result["Microsoft Agent Framework"]["content_count"] == 0
    assert result["CrewAI"]["content_count"] == 1
    assert result["Context compression"]["content_count"] == 1


def test_design_status_is_attached_from_same_original_document_only():
    module = load_script("expand_agent_hotspots")
    body = SimpleNamespace(id="body", source="official", path="design.md", start=50,
                           text="The proposed tool execution architecture.")
    header = SimpleNamespace(id="header", source="official", path="design.md", start=1,
                             text="---\nstatus: proposed\n---")
    other = SimpleNamespace(id="other", source="other", path="design.md", start=1,
                            text="status: accepted")
    late = SimpleNamespace(id="late", source="official", path="design.md", start=500,
                           text="status: accepted")
    result = module.include_document_status([body], [header, other, late])
    assert [c.id for c in result] == ["body", "header"]
    assert body.text == "The proposed tool execution architecture."
    assert module.include_document_status([body, header], [header]) == [body, header]


def test_protocol_migration_notice_is_kept_with_historical_body():
    module = load_script("expand_agent_hotspots")
    body = SimpleNamespace(id="body", source="acp", path="docs/runs.md", start=20,
                           text="The historical protocol has runs.")
    notice = SimpleNamespace(id="notice", source="acp", path="README.md", start=5,
                             text="ACP has merged with A2A.")
    other = SimpleNamespace(id="other", source="unrelated", path="README.md", start=1,
                            text="This project is archived.")
    assert module.include_document_status([body], [notice, other]) == [body, notice]


def test_python_symbol_boundaries_use_standard_ast_and_preserve_decorators(monkeypatch):
    import builtins

    from interview_question_bank.chunking import symbol_ranges

    original_import = builtins.__import__

    def guard(name, *args, **kwargs):
        if name == "tree_sitter_language_pack":
            raise AssertionError("Python must not load the native grammar")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guard)
    text = (
        "import os\n\n@decorate\nasync def run():\n    return 1\n\n"
        "class Session:\n    pass\n"
    )
    ranges = symbol_ranges(text, "example.py")
    lines = text.splitlines(keepends=True)
    parts = ["".join(lines[a:b]) for a, b in ranges]
    assert any(p.startswith("@decorate\nasync def run") for p in parts)
    assert any(p.startswith("class Session:") for p in parts)
    assert "".join(parts) == text
    assert symbol_ranges("def incomplete(\n", "snippet.py") == [(0, 1)]


def test_review_can_only_unpublish_current_batch_not_baseline(env, tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    module = load_script("review_harness_questions")
    bank = module.QuestionBank(
        env.s, store=env.store, embeddings=env.embeddings, index=env.index,
    )
    question = load_script("expand_agent_hotspots").InterviewQuestion(
        title="New evidence review", difficulty="基础", knowledge_points=["review"],
        answer="A supported answer", key_points=["A key point"],
        origin="基于资料生成", evidence_ids=["raw"],
    )
    with env.store.db:
        for qid in ("baseline", "added"):
            env.store.db.execute("INSERT INTO question_records VALUES(?,?,?,?,?)",
                                 (qid, question.model_dump_json(), "[]", 0, time.time()))
            env.store.db.execute("INSERT INTO question_catalog VALUES(?,?,?,?,?,?,?,?,?)",
                                 (qid, qid, "topic", "Topic", 1, "llm_verified", "[]",
                                  env.s.embedding_model, "current"))
    deleted = []
    bank.index = SimpleNamespace(
        ensure=lambda: None, _collection="questions",
        _client=SimpleNamespace(
            delete=lambda collection, ids, **kw: deleted.extend(ids),
            get=lambda *args, **kw: [],
        ),
    )
    monkeypatch.setattr(module, "QuestionBank", lambda _: bank)
    (tmp_path / "research.json").write_text(json.dumps({
        "baseline": {"question_ids": ["baseline"]},
    }))
    (tmp_path / "build.json").write_text(json.dumps({
        "per_facet": 3,
        "batches": [dict(topic="topic", facet="facet", ids=["added"], added=1)],
    }))
    plan = dict(version="current", topics=[dict(id="topic", facets=["facet"])])
    with pytest.raises(ValueError, match="only unpublish"):
        module.review(tmp_path, [dict(id="baseline", reason="must not delete")], plan)
    assert {r["id"] for r in bank.rows()} == {"baseline", "added"}
    assert not deleted
    module.review(tmp_path, [dict(id="added", reason="Unsupported absolute claim")], plan)
    assert {r["id"] for r in bank.rows()} == {"baseline"}
    assert deleted == ["added"]
    report = json.loads((tmp_path / "build.json").read_text())
    assert report["batches"][0]["model_verified_added_before_review"] == 1
    assert report["batches"][0]["added"] == 0
    assert report["batches"][0]["ids"] == []
    assert report["batches"][0]["model_verified_ids_before_review"] == ["added"]
    assert json.loads((tmp_path / "review-exclusions.json").read_text())[0]["question"]["id"] == (
        "added"
    )


def test_reviewed_expansion_cannot_refill_explicitly_rejected_questions(tmp_path, monkeypatch):
    module = load_script("expand_agent_hotspots")
    plan = dict(version="current", topics=[])
    (tmp_path / "research.json").write_text(json.dumps({
        "baseline": {"question_ids": []},
    }))
    (tmp_path / "build.json").write_text(json.dumps({
        "plan_version": "current", "per_facet": 3, "plan_digest": module.digest(plan),
        "manual_review": {"excluded": ["rejected"]},
    }))
    monkeypatch.setattr(module, "get_settings", lambda: SimpleNamespace(llm_model="glm-5.3-flashx"))
    monkeypatch.setattr(module, "QuestionBank", lambda _: None)
    with pytest.raises(ValueError, match="Reviewed expansion is closed"):
        module.build(tmp_path, plan, per_facet=3)
