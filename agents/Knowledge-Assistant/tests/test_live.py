"""Opt-in real-service tests. Never substitute mock providers for failed real calls."""

import json
import os
import subprocess
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import pexpect
import pytest

from knowledge_assistant.config import Settings
from knowledge_assistant.models import Answer, Bundle, Document
from knowledge_assistant.retrieval import Embeddings, Knowledge, MilvusIndex
from knowledge_assistant.storage import Store

ROOT = os.path.dirname(os.path.dirname(__file__))
KA = os.path.join(ROOT, ".venv", "bin", "ka")


@pytest.mark.live
@pytest.mark.skipif(os.getenv("KA_RERANK") != "1", reason="set KA_RERANK=1 for real BGE")
def test_real_cross_encoder_prefers_relevant_evidence():
    import math

    from knowledge_assistant.models import Chunk
    from knowledge_assistant.reranking import Reranker

    # Explicit synthetic model probe, not a harvested repository or project QA result.
    chunks = [
        Chunk(str(i), "", text, "", "", 1, 1, 0, 0)
        for i, text in enumerate(
            [
                "ctx.request_id returns the unique ID of the current MCP request.",
                "Milvus is a database for storing and searching dense vectors.",
            ]
        )
    ]
    reranker = Reranker(Settings())
    scores = reranker.scores("FastMCP Context 如何读取当前请求 ID？", chunks)
    assert len(scores) == 2 and all(math.isfinite(s) for s in scores)
    assert scores[0] > scores[1]
    assert reranker.windowing["windowed_ids"] == []


@pytest.mark.live
@pytest.mark.skipif(os.getenv("KA_BROWSER") != "1", reason="set KA_BROWSER=1 for real Chromium")
def test_real_browser_repository_body():
    from knowledge_assistant.network import BrowserReader, SafeHTTP

    url = (
        "https://github.com/adongwanai/AgentGuide/blob/"
        "9a68e14bb4e44275c6e144b2e2c86ff4b02457ac/README.md"
    )
    text = BrowserReader(SafeHTTP()).read(url)
    assert "AgentGuide" in text and "LangGraph" in text


@pytest.mark.live
@pytest.mark.skipif(
    os.getenv("KA_BROWSER_REPO") != "1",
    reason="set KA_BROWSER_REPO=1 for browser repository import",
)
def test_real_browser_repository_commit_and_source_lines():
    from knowledge_assistant.collectors import Collectors

    settings = Settings(github_import_mode="browser", github_tree_pages=0, max_files=1)
    bundle = Collectors(settings).collect("infiniflow/ragflow", role="project")
    assert bundle.repo == "infiniflow/ragflow" and len(bundle.commit) == 40
    assert bundle.coverage["transport"] == "browser"
    assert bundle.documents and all(
        f"/blob/{bundle.commit}/" in d.url and d.role == "project" and d.path
        for d in bundle.documents
    )
    assert "ragflow" in bundle.documents[0].text.lower()


@pytest.mark.live
@pytest.mark.skipif(
    os.getenv("KA_CATALOG_QA") != "1", reason="set KA_CATALOG_QA=1 after the real catalog import"
)
def test_real_imported_catalog_qa_and_cache(tmp_path):
    settings = Settings()
    expected = {r["repo"] for r in json.loads(settings.seeds_file.read_text())}
    sources = Store(settings.data_dir).sources()
    assert expected <= {r["repo"] for r in sources}, "Import the entire catalog first."
    assert all(
        json.loads(r["coverage"])["transport"] == "browser"
        for r in sources
        if r["repo"] in expected
    )
    evidence = Path(os.getenv("KA_EVIDENCE_DIR", str(tmp_path)))
    evidence.mkdir(parents=True, exist_ok=True)
    model = settings.model_settings()["model"]
    (evidence / "model.json").write_text(json.dumps({"model": model}))

    def command(label, *args, override=None):
        run = subprocess.run(
            [KA, *args],
            env=dict(os.environ, LLM_MODEL=model, **(override or {})),
            capture_output=True,
            text=True,
            timeout=600,
        )
        (evidence / f"{label}.stdout").write_text(run.stdout)
        (evidence / f"{label}.stderr").write_text(run.stderr)
        assert run.returncode == 0, run.stdout + run.stderr
        return json.loads(run.stdout)

    question = (
        "根据本地已入库 README，ai-boost/awesome-harness-engineering 和 "
        "infiniflow/ragflow 分别是什么？请对比定位，区分资料汇编与实际项目，并附来源。"
    )
    # Clear only this acceptance question, never unrelated user cache entries.
    from knowledge_assistant.cache import Cache, cache_key

    store = Store(settings.data_dir)
    Cache(settings).client.delete(cache_key(question, [], settings, store.version, store.corpus_id))
    first = command("01-new-projects", "chat", "-q", question, "--local-only", "--json")
    assert first["route"] == "local" and first["sufficient"] and first["persisted"]
    assert first["contexts"] and first["retrieval"]
    if settings.rerank_enabled:
        assert all(
            t["reranker"] == settings.reranker_model and t["scores"] for t in first["retrieval"]
        )
    new_repos = {"ai-boost/awesome-harness-engineering", "infiniflow/ragflow"}
    assert new_repos <= {c["repo"] for c in first["citations"]}
    assert all(
        len(c["commit"]) == 40 and f"/blob/{c['commit']}/" in c["url"] and "#L" in c["url"]
        for c in first["citations"]
    )
    assert {c["role"] for c in first["citations"]} >= {"third_party", "project"}
    cached = command(
        "02-cache",
        "chat",
        "-q",
        question,
        "--local-only",
        "--json",
        override={"MILVUS_URI": "http://127.0.0.1:1", "LLM_API_KEY": ""},
    )
    assert cached["route"] == "cache" and cached["text"] == first["text"]
    follow = command(
        "03-context",
        "chat",
        "--session",
        first["session"],
        "-q",
        "其中哪个是资料汇编？哪个主要围绕 RAG 和 Agent 构建产品？只根据这些资料回答。",
        "--local-only",
        "--json",
    )
    assert follow["route"] == "local" and follow["sufficient"] and follow["citations"]
    assert new_repos <= {c["repo"] for c in follow["citations"]}


@pytest.mark.live
@pytest.mark.skipif(os.getenv("KA_SERVICES") != "1", reason="set KA_SERVICES=1 for real databases")
def test_real_milvus_embedding_redis_and_restart(tmp_path):
    import redis

    namespace = "katest_" + uuid.uuid4().hex[:8]
    s = Settings(
        data_dir=tmp_path,
        index_namespace=namespace,
        model_cache_dir=os.path.join(ROOT, "data", "models"),
    )
    e = Embeddings(s)
    index = MilvusIndex(s, e)
    store = Store(tmp_path)
    knowledge = Knowledge(s, store, e, index)
    # This is explicitly a synthetic integration fixture, not a harvested project source.
    doc = Document(
        "https://example.com/integration-fixture",
        "https://example.com/integration-fixture",
        "# Integration fixture\nLangGraph uses graph state for agent workflows.\n"
        "Milvus stores embedding vectors. Redis caches answers.\n",
        path="README.md",
    )
    bundle = Bundle(doc.source, [doc], {"selected": ["README.md"], "partial": False})
    key = namespace + ":probe"
    client = redis.Redis.from_url(s.redis_url.get_secret_value())
    try:
        assert knowledge.ingest(bundle)["embedded"] > 0
        assert knowledge.ingest(bundle)["embedded"] == 0
        assert knowledge.retrieve("哪个系统保存向量？")
        restarted = Knowledge(s, Store(tmp_path), e, index)
        assert restarted.retrieve("Where are embedding vectors stored?")
        from knowledge_assistant.cache import Cache

        cache = Cache(s)
        cache.put(
            key,
            Answer(
                "Synthetic integration answer [1]",
                [dict(store.active_chunks()[0].to_dict(), number=1)],
                "local",
                True,
            ),
        )
        assert cache.get(key).route == "cache"
        assert 0 < client.ttl(key) <= s.cache_ttl
    finally:
        client.delete(key)
        if index._client and index._collection:
            index._client.drop_collection(index._collection)


@pytest.mark.live
@pytest.mark.skipif(
    os.getenv("KA_LIVE") != "1", reason="set KA_LIVE=1 for real terminal/provider E2E"
)
def test_real_terminal_seed_local_cache_online_reuse(tmp_path):
    # Credentials are deliberately not fetched from any unrelated project.
    settings = Settings()
    assert settings.model_settings()["api_key"], "LLM credentials missing"
    assert settings.tavily_api_key.get_secret_value(), "Search credentials missing"
    env = dict(
        os.environ,
        DATA_DIR=str(tmp_path),
        MODEL_CACHE_DIR=os.path.join(ROOT, "data", "models"),
        INDEX_NAMESPACE="kae2e_" + uuid.uuid4().hex[:8],
        MAX_FILES="2",
    )

    def command(*args):
        run = subprocess.run([KA, *args], env=env, capture_output=True, text=True, timeout=600)
        assert run.returncode == 0, run.stdout + run.stderr
        return run.stdout

    # Full supplied catalog; MAX_FILES only bounds the initial acceptance run.
    command("seed")
    question = "LangChain 是什么？请引用项目自身资料回答。"
    first = json.loads(command("chat", "-q", question, "--local-only", "--json"))
    assert first["route"] == "local" and first["sufficient"] and first["citations"]
    cached = json.loads(command("chat", "-q", question, "--local-only", "--json"))
    assert cached["route"] == "cache"
    external = "FastMCP 的 Context 参数如何读取请求上下文？请查找官方资料并引用。"
    online = json.loads(command("chat", "-q", external, "--json"))
    assert online["route"] == "online" and online["persisted"] and online["sufficient"]
    assert online["citations"]
    # A different wording avoids satisfying this assertion only through the answer cache.
    reused = json.loads(
        command(
            "chat",
            "-q",
            "请根据现有资料解释 FastMCP Context 的请求上下文读取方式。",
            "--local-only",
            "--json",
        )
    )
    assert reused["route"] == "local" and reused["sufficient"] and reused["citations"]
    child = pexpect.spawn(KA, ["chat", "--local-only"], env=env, encoding="utf-8", timeout=60)
    try:
        child.expect("ka> ")
        child.sendline("/sessions")
        child.expect("ka> ")
        child.sendline("/new")
        child.expect("ka> ")
        child.sendline("/quit")
        child.expect(pexpect.EOF)
    finally:
        child.close(force=True)


@pytest.mark.live
@pytest.mark.skipif(
    os.getenv("KA_QA_LIVE") != "1", reason="set KA_QA_LIVE=1 for real web-based QA E2E"
)
def test_real_web_qa_cache_context_online_and_reuse(tmp_path):
    """Separate web QA acceptance from bounded repository seed acceptance."""
    settings = Settings()
    assert settings.model_settings()["api_key"], "LLM credentials missing"
    assert settings.tavily_api_key.get_secret_value(), "Search credentials missing"
    evidence = Path(os.getenv("KA_EVIDENCE_DIR", str(tmp_path / "evidence")))
    evidence.mkdir(parents=True, exist_ok=True)
    env = dict(
        os.environ,
        DATA_DIR=str(evidence / "knowledge"),
        MODEL_CACHE_DIR=os.path.join(ROOT, "data", "models"),
        INDEX_NAMESPACE="kaqa_" + uuid.uuid4().hex[:8],
        MAX_FILES="2",
        LLM_MODEL=settings.model_settings()["model"],
    )
    (evidence / "environment.json").write_text(
        json.dumps(
            {k: env[k] for k in ("DATA_DIR", "MODEL_CACHE_DIR", "INDEX_NAMESPACE", "LLM_MODEL")},
            indent=2,
        )
    )

    def command(label, *args, override=None, success=True):
        started = time.monotonic()
        run = subprocess.run(
            [KA, *args],
            env=dict(env, **(override or {})),
            capture_output=True,
            text=True,
            timeout=900,
        )
        (evidence / f"{label}.stdout").write_text(run.stdout)
        (evidence / f"{label}.stderr").write_text(run.stderr)
        with (evidence / "commands.jsonl").open("a") as stream:
            stream.write(
                json.dumps(
                    {
                        "label": label,
                        "args": args,
                        "returncode": run.returncode,
                        "seconds": round(time.monotonic() - started, 2),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
        print(f"{label}: exit={run.returncode}", flush=True)
        if success:
            assert run.returncode == 0, run.stdout + run.stderr
        return run

    url = "https://docs.langchain.com/oss/python/langchain/overview"
    imported = json.loads(command("01-ingest", "ingest", url).stdout)
    assert imported["embedded"] > 0
    repeated = json.loads(command("02-idempotent", "ingest", url).stdout)
    assert repeated["embedded"] == 0 and repeated["version"] == imported["version"]
    question = "LangChain 的 create_agent 是什么？它的 harness 由哪些部分组成？请引用资料。"
    first = json.loads(command("03-local", "chat", "-q", question, "--json").stdout)
    assert first["route"] == "local" and first["sufficient"] and first["citations"]
    assert "create_agent" in first["text"] and not first["warnings"]
    assert first["contexts"] and first["retrieval"]
    if settings.rerank_enabled:
        assert all(
            t["reranker"] == settings.reranker_model and t["scores"] for t in first["retrieval"]
        )
    # A real Redis hit must still work when both the vector endpoint and LLM key are absent.
    cached = json.loads(
        command(
            "04-cache-with-unavailable-vector-and-model",
            "chat",
            "-q",
            question,
            "--json",
            override={"MILVUS_URI": "http://127.0.0.1:1", "LLM_API_KEY": ""},
        ).stdout
    )
    assert cached["route"] == "cache" and cached["text"] == first["text"]
    follow = json.loads(
        command(
            "05-context",
            "chat",
            "--session",
            first["session"],
            "--local-only",
            "-q",
            "它与 LangGraph 的定位有什么区别？只根据现有资料回答。",
            "--json",
        ).stdout
    )
    assert follow["route"] == "local" and follow["sufficient"] and follow["citations"]
    assert "LangGraph" in follow["text"] and "LangChain" in follow["text"]
    external = (
        "FastMCP 的工具函数如何使用 Context 参数读取请求上下文？"
        "请查找 FastMCP 官方资料，给出注入 Context 和读取请求 ID 的代码。"
    )
    missing = json.loads(
        command("06-insufficient", "chat", "-q", external, "--local-only", "--json").stdout
    )
    assert not missing["sufficient"] and missing["route"] == "local"
    online = json.loads(command("07-online", "chat", "-q", external, "--json").stdout)
    assert online["route"] == "online" and online["persisted"] and online["sufficient"]
    assert online["citations"] and "Context" in online["text"]
    assert "request_id" in online["text"] and "@mcp.tool" in online["text"]
    assert any(urlsplit(c["url"]).hostname == "gofastmcp.com" for c in online["citations"])
    reused = json.loads(
        command(
            "08-reuse",
            "chat",
            "-q",
            "只用现有资料解释 FastMCP Context 的注入方法，以及怎样取得请求 ID。",
            "--local-only",
            "--json",
        ).stdout
    )
    assert reused["route"] == "local" and reused["persisted"] and reused["sufficient"]
    assert reused["citations"] and reused["session"] != online["session"]
    assert "request_id" in reused["text"]
    invalidated = json.loads(command("09-invalidated", "chat", "-q", question, "--json").stdout)
    assert invalidated["route"] == "local" and invalidated["sufficient"]
    command("10-sources", "sources", "--json")
    child = pexpect.spawn(KA, ["chat", "--local-only"], env=env, encoding="utf-8", timeout=180)
    try:
        with (evidence / "11-terminal.txt").open("w") as transcript:
            child.logfile = transcript
            child.expect("ka> ")
            child.sendline(question)
            child.expect("来源路径: cache")
            child.expect("ka> ")
            child.sendline("/sources")
            child.expect("ka> ")
            child.sendline("/sessions")
            child.expect("ka> ")
            child.sendline("/new")
            child.expect("ka> ")
            child.sendline("/resume " + first["session"])
            child.expect("ka> ")
            child.sendline("/quit")
            child.expect(pexpect.EOF)
        child.close()
        assert child.exitstatus == 0
    finally:
        child.close(force=True)
