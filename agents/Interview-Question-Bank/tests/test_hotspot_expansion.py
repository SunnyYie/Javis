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
