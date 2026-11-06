import time

import pytest
from conftest import bundle

from knowledge_assistant.agent import Assistant
from knowledge_assistant.models import ServiceError


def assistant(env):
    return Assistant(
        env.s,
        store=env.store,
        knowledge=env.knowledge,
        models=env.models,
        collectors=env.collectors,
        cache=env.cache,
    )


def test_local_answer_and_cache_hit_do_not_search(env):
    env.knowledge.ingest(bundle())
    a = assistant(env)
    result = a.ask("What is LangGraph?")
    assert result.route == "local" and result.sufficient
    assert result.citations[0]["number"] == 1
    searches = env.index.searches
    calls = len(env.models.calls)
    result = a.ask("What is LangGraph?")
    assert result.route == "cache"
    assert env.index.searches == searches
    assert len(env.models.calls) == calls
    assert env.collectors.calls == []


def test_cache_isolated_by_history_question_and_version(env):
    env.knowledge.ingest(bundle())
    a = assistant(env)
    assert a.ask("What is it?").route == "local"
    assert a.ask("What is it?", [{"question": "Milvus", "answer": "database"}]).route == "local"
    assert a.ask("Different?").route == "local"
    env.knowledge.ingest(bundle("new text", commit="def"))
    assert a.ask("What is it?").route == "local"


def test_local_only_without_evidence_never_searches(env):
    result = assistant(env).ask("missing knowledge", local_only=True)
    assert not result.sufficient and result.citations == []
    assert env.collectors.calls == []
    assert not env.cache.rows


def test_online_ingest_then_reuse_locally(env):
    url = "https://github.com/new/project"
    env.collectors.urls = [url]
    env.collectors.bundles[url] = bundle(source=url)
    a = assistant(env)
    result = a.ask("LangGraph")
    assert result.route == "online" and result.persisted and result.sufficient
    assert env.store.version == 1
    env.cache.rows.clear()
    env.collectors.calls.clear()
    result = a.ask("Tell me about LangGraph", local_only=True)
    assert result.route == "local" and result.sufficient
    assert env.collectors.calls == []


def test_search_errors_do_not_become_no_results(env):
    def blocked(_):
        raise ServiceError("github", "rate_limited")

    env.collectors.github.search = blocked
    result = assistant(env).ask("missing")
    assert not result.sufficient
    assert any("rate_limited" in w for w in result.warnings)
    assert not env.cache.rows


def test_unsaved_evidence_remains_explicit_and_not_cached(env):
    url = "https://github.com/new/project"
    env.collectors.urls = [url]
    env.collectors.bundles[url] = bundle(source=url)
    env.index.write_failure = True
    result = assistant(env).ask("LangGraph")
    assert result.sufficient and not result.persisted
    assert env.store.version == 0 and not env.cache.rows
    assert any("not saved" in w for w in result.warnings)


def test_insufficient_result_does_not_loop_forever(env):
    env.knowledge.ingest(bundle("only loosely related material"))
    env.models.verdict = False
    result = assistant(env).ask("uncovered")
    assert result.route == "online" and not result.sufficient
    assert len([c for c in env.collectors.calls if c[0] == "search"]) == 4
    assert not env.cache.rows


def test_stale_local_only_is_labelled(env):
    env.knowledge.ingest(bundle(fetched_at=time.time() - env.s.repo_ttl - 1))
    result = assistant(env).ask("LangGraph", local_only=True)
    assert any("Stale" in w for w in result.warnings)
    assert env.collectors.calls == []


def test_failed_refresh_keeps_old_content(env):
    env.knowledge.ingest(bundle(fetched_at=time.time() - env.s.repo_ttl - 1))
    result = assistant(env).ask("LangGraph")
    assert any("using stale" in w for w in result.warnings)
    assert env.store.version == 1


def test_milvus_outage_does_not_silently_go_online(env):
    env.index.available = False
    with pytest.raises(ServiceError):
        assistant(env).ask("LangGraph")
    assert env.collectors.calls == []


@pytest.mark.parametrize("output", ["unsupported answer", "wrong reference [99]"])
def test_missing_or_invalid_references_disable_cache(env, output):
    env.knowledge.ingest(bundle())
    env.models.output = output
    result = assistant(env).ask("LangGraph")
    assert not result.sufficient and not env.cache.rows
    assert any("Citation validation" in w for w in result.warnings)


def test_cancel_during_generation_never_caches(env):
    env.knowledge.ingest(bundle())

    def cancel(*args):
        raise KeyboardInterrupt

    env.models.answer = cancel
    with pytest.raises(KeyboardInterrupt):
        assistant(env).ask("LangGraph")
    assert not env.cache.rows
