"""Real-service acceptance. Failures must never fall back to test doubles."""

import json
import os
import subprocess
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pexpect
import pytest

from interview_question_bank.cache import PROMPT_VERSION, Cache
from interview_question_bank.collectors import Collectors
from interview_question_bank.config import ROOT, Settings
from interview_question_bank.models import Answer
from interview_question_bank.retrieval import Knowledge
from interview_question_bank.storage import Store


@pytest.mark.live
@pytest.mark.skipif(os.getenv("IQB_SERVICES") != "1", reason="requires real databases/embedding")
def test_real_services_persistence_dedup_atomic_cache_and_expiry(tmp_path):
    import redis

    configured = Settings()
    s = Settings(
        data_dir=tmp_path, index_namespace="iqbtest_" + uuid.uuid4().hex[:8],
        model_cache_dir=configured.model_cache_dir or ROOT / "data" / "models",
    )
    knowledge = Knowledge(s, Store(tmp_path))
    client = redis.Redis.from_url(s.redis_url.get_secret_value(), decode_responses=True)
    index = knowledge.index
    try:
        # Real official body, not synthetic text or a provider search snippet.
        bundle = Collectors(s).collect("https://redis.io/docs/latest/develop/using-commands/pipelining/")
        first = knowledge.ingest(bundle)
        assert first["embedded"] > 0
        assert knowledge.ingest(bundle)["embedded"] == 0
        restarted = Knowledge(s, Store(tmp_path))
        chunks = restarted.retrieve("Redis pipelining round trip time")
        assert chunks
        assert restarted.last_trace["reranker_enabled"]
        assert restarted.last_trace["scores"]
        assert restarted.last_trace["final_ids"] == [c.id for c in chunks]
        assert restarted.store.version == first["version"]
        cache = Cache(s)
        corpus = restarted.store.corpus_id
        result = Answer("Real service integration only", [chunks[0].to_dict()], "local", True)

        def register(_):
            cache.put("concurrent", corpus, first["version"], "Redis", result)

        with ThreadPoolExecutor(max_workers=6) as executor:
            list(executor.map(register, range(12)))
        counter, key = cache.keys("concurrent", corpus, first["version"])
        assert int(client.hget(counter, "count")) == 12
        assert 0 < client.ttl(counter) <= s.hot_window
        assert cache.get("concurrent", corpus, first["version"]).route == "cache"
        assert cache.get("concurrent", corpus, first["version"] + 1) is None
        client.expire(counter, 1)
        time.sleep(1.2)
        client.delete(key)
        cache.put("concurrent", corpus, first["version"], "Redis", result)
        assert int(client.hget(counter, "count")) == 1 and client.get(key) is None
        if os.getenv("IQB_RESTART") == "1":
            probe = cache.prefix(corpus) + "restart-probe"
            nonce = uuid.uuid4().hex
            client.set(probe, nonce, ex=300)
            subprocess.run(
                ["docker", "compose", "restart", "milvus", "redis"], cwd=ROOT,
                check=True, timeout=120,
            )
            subprocess.run(
                ["docker", "compose", "up", "-d", "--wait"], cwd=ROOT,
                check=True, timeout=180,
            )
            fresh = Knowledge(s, Store(tmp_path))
            recovered = fresh.retrieve("Redis pipelining round trip time")
            assert {c.id for c in recovered} == {c.id for c in chunks}
            fresh_redis = redis.Redis.from_url(
                s.redis_url.get_secret_value(), decode_responses=True,
            )
            assert fresh_redis.get(probe) == nonce
    finally:
        for key in client.scan_iter(match=cache.prefix(corpus) + "*") if "cache" in locals() else []:
            client.delete(key)
        if index._client and index._collection:
            index._client.drop_collection(index._collection)


def evidence_root(label):
    root = Path(os.getenv("IQB_EVIDENCE_DIR", str(ROOT / "data" / "acceptance")))
    path = root / label
    path.mkdir(parents=True, exist_ok=False)
    return path


@pytest.mark.live
@pytest.mark.parametrize("provider", ["github", "tavily"])
@pytest.mark.skipif(os.getenv("IQB_PROVIDERS") != "1", reason="requires real GitHub/Tavily")
def test_real_provider_search_and_body(provider):
    root = evidence_root(provider)
    collectors = Collectors(Settings())
    adapter = collectors.github if provider == "github" else collectors.web
    try:
        urls = adapter.search("Redis interview questions")
        assert urls, "Provider returned no results"
        (root / "search.json").write_text(json.dumps(urls, ensure_ascii=False, indent=2))
        errors = []
        for url in urls[:3]:
            try:
                bundle = collectors.collect(url, query="Redis", file_limit=1)
                assert bundle.documents and len(bundle.documents[0].text) > 100
                (root / "body.json").write_text(json.dumps({
                    "source": bundle.source, "commit": bundle.commit,
                    "documents": [document.__dict__ for document in bundle.documents],
                }, ensure_ascii=False, indent=2))
                return
            except Exception as exc:
                errors.append(str(exc) if hasattr(exc, "category") else type(exc).__name__)
        raise AssertionError(f"No readable provider body: {errors}")
    except Exception as exc:
        (root / "failure.txt").write_text(
            str(exc) if hasattr(exc, "category") or isinstance(exc, AssertionError)
            else type(exc).__name__
        )
        raise


@pytest.mark.live
@pytest.mark.skipif(os.getenv("IQB_LIVE") != "1", reason="requires real model/search/CLI")
def test_real_terminal_empty_to_online_local_context_hot_and_invalidation():
    root = evidence_root("e2e")
    s = Settings()
    assert s.llm_api_key.get_secret_value() and s.tavily_api_key.get_secret_value()
    env = dict(
        os.environ, DATA_DIR=str(root / "knowledge"),
        INDEX_NAMESPACE="iqbe2e_" + uuid.uuid4().hex[:8],
        MODEL_CACHE_DIR=str(s.model_cache_dir or ROOT / "data" / "models"),
        LLM_MODEL=s.llm_model,
    )
    executable = str(ROOT / ".venv" / "bin" / "iqb")
    manifest = {
        key: env[key] for key in ("DATA_DIR", "INDEX_NAMESPACE", "MODEL_CACHE_DIR", "LLM_MODEL")
    }
    (root / "environment.json").write_text(json.dumps(
        dict(manifest, PROMPT_VERSION=PROMPT_VERSION), indent=2,
    ))

    def command(label, *args, override=None):
        start = time.monotonic()
        result = subprocess.run(
            [executable, *args], env=dict(env, **(override or {})), capture_output=True,
            text=True, timeout=1800,
        )
        (root / f"{label}.stdout").write_text(result.stdout)
        (root / f"{label}.stderr").write_text(result.stderr)
        with (root / "commands.jsonl").open("a") as stream:
            stream.write(json.dumps({
                "label": label, "args": args, "exit_code": result.returncode,
                "seconds": round(time.monotonic() - start, 2),
            }, ensure_ascii=False) + "\n")
        print(f"{label}: exit={result.returncode}", flush=True)
        assert result.returncode == 0, result.stdout + result.stderr
        return json.loads(result.stdout)

    initial = command("01-empty", "chat", "-q", "Redis 持久化面试题", "--local-only", "--json")
    assert not initial["sufficient"] and initial["questions"] == []
    online = command("02-online", "chat", "-q", "Redis 持久化面试题", "--json")
    assert online["route"] == "online" and online["sufficient"] and online["persisted"]
    assert len(online["questions"]) == 5 and online["citations"]
    assert sorted(q["difficulty"] for q in online["questions"]) == sorted(
        ["基础", "基础", "进阶", "进阶", "高阶"]
    )
    reused = command(
        "03-local-reuse", "chat", "-q", "请给出五道关于 Redis RDB 与 AOF 的面试题",
        "--local-only", "--json",
    )
    assert reused["route"] == "local" and reused["sufficient"] and reused["citations"]
    explained = command(
        "04-context", "chat", "-q", "解释第二题", "--session", reused["session"],
        "--local-only", "--json",
    )
    assert explained["request"]["mode"] == "explain" and explained["explanation"]
    assert explained["sufficient"] and explained["citations"]
    # Fresh sessions deliberately keep each request context identical.
    question = "请给出五道关于 Redis RDB 与 AOF 的面试题"
    for n in (2, 3):
        value = command(f"05-hot-{n}", "chat", "-q", question, "--local-only", "--json")
        assert value["route"] == "local" and value["sufficient"]
        assert value["hot_count"] == n
        assert value["cache_promoted"] == (n == 3)
    cached = command(
        "06-cache-with-providers-unavailable", "chat", "-q", question, "--local-only", "--json",
        override={
            "LLM_API_KEY": "",
            "MILVUS_URI": "http://127.0.0.1:1",
            # Cache identity intentionally binds gateway, so preserve its actual value.
            "LLM_BASE_URL": s.llm_base_url,
        },
    )
    assert cached["route"] == "cache" and cached["hot_count"] == 4
    assert cached["questions"] == value["questions"]
    command(
        "07-update-source", "ingest",
        "https://redis.io/docs/latest/develop/using-commands/pipelining/",
    )
    invalidated = command("08-invalidated", "chat", "-q", question, "--local-only", "--json")
    assert invalidated["route"] == "local" and invalidated["sufficient"]
    store = Store(Path(env["DATA_DIR"]))
    events = [dict(row) for row in store.db.execute("SELECT * FROM events")]
    (root / "events.json").write_text(json.dumps(events, ensure_ascii=False, indent=2))
    assert any(row["category"].startswith("search_ok:") for row in events)
    child = pexpect.spawn(executable, ["chat", "--local-only"], env=env, encoding="utf-8", timeout=90)
    transcript = []
    try:
        child.expect("iqb> ")
        for text in ("/sessions", "/new", f"/resume {reused['session']}", "/sources"):
            child.sendline(text)
            child.expect("iqb> ")
            transcript.append(child.before)
        child.sendline("/quit")
        child.expect(pexpect.EOF)
    finally:
        child.close(force=True)
        (root / "terminal.txt").write_text("\n".join(transcript))
    assert child.exitstatus == 0
