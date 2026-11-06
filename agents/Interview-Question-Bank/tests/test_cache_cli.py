import copy
import json
import time

import pytest
from typer.testing import CliRunner

from interview_question_bank.cache import Cache, normalize, request_id
from interview_question_bank.cli import app
from interview_question_bank.models import Answer, InterviewRequest, difficulty_targets


class MemoryRedis:
    """Test double for branch tests, not proof of Redis atomicity."""
    def __init__(self):
        self.values, self.hashes, self.expires = {}, {}, {}

    def get(self, key):
        return self.values.get(key)

    def exists(self, key):
        return key in self.hashes

    def hincrby(self, key, field, n):
        self.hashes[key][field] += n
        return self.hashes[key][field]

    def eval(self, script, nkeys, counter, key, window, label, threshold, ttl, payload):
        if counter not in self.hashes:
            self.hashes[counter] = {"count": 0, "question": label}
            self.expires[counter] = window
        count = self.hincrby(counter, "count", 1)
        if count >= threshold and ttl > 0:
            self.values[key] = payload
            self.expires[key] = ttl
        return count

    def scan_iter(self, **kwargs):
        return iter(self.hashes)

    def hgetall(self, key):
        return self.hashes[key]

    def ttl(self, key):
        return self.expires[key]


def answer():
    return Answer("答案", [{"id": "evidence", "expires_at": time.time() + 1000}],
                  "local", True)


def test_third_success_promotes_fourth_hits_and_window_does_not_slide(env):
    client = MemoryRedis()
    cache = Cache(env.s, client=client)
    for i in range(3):
        assert cache.get("r", "corpus", 1) is None
        result = answer()
        cache.put("r", "corpus", 1, "Redis", result)
        assert result.hot_count == i + 1
        assert result.cache_promoted == (i == 2)
    result = cache.get("r", "corpus", 1)
    assert result.route == "cache" and result.hot_count == 4
    assert cache.hot("corpus")[0]["count"] == 4
    assert client.expires[cache.keys("r", "corpus", 1)[0]] == 86400
    assert cache.get("r", "corpus", 2) is None
    assert cache.get("r", "other", 1) is None
    assert cache.get("r", "corpus", 1).hot_count == 5


@pytest.mark.parametrize("change", ["insufficient", "unsaved", "expired", "no_citations"])
def test_ineligible_results_never_increment_or_promote(env, change):
    client = MemoryRedis()
    cache = Cache(env.s, client=client)
    result = answer()
    if change == "insufficient":
        result.sufficient = False
    elif change == "unsaved":
        result.persisted = False
    elif change == "expired":
        result.citations[0]["expires_at"] = 0
    else:
        result.citations = []
    cache.put("r", "corpus", 1, "Redis", result)
    assert not client.values and not client.hashes


def test_normalization_and_keys_keep_context_and_mode_isolated(env, monkeypatch):
    import interview_question_bank.cache as cache_module

    assert normalize(" ＲＥＤＩＳ \t 面试题 ") == "redis 面试题"
    key = request_id("Redis", [], env.s, "corpus")
    assert key == request_id("ＲＥＤＩＳ", [], env.s, "corpus")
    assert key != request_id("Redis", [], env.s, "corpus", True)
    assert key != request_id("Redis", [{"answer": "first"}], env.s, "corpus")
    other = copy.deepcopy(env.s)
    other.llm_model = "other"
    assert key != request_id("Redis", [], other, "corpus")
    monkeypatch.setattr(cache_module, "PROMPT_VERSION", "changed")
    assert key != request_id("Redis", [], env.s, "corpus")


def test_corrupt_cache_skips(env):
    client, warnings = MemoryRedis(), []
    cache = Cache(env.s, warnings.append, client)
    client.values[cache.keys("r", "c", 1)[1]] = "{bad"
    assert cache.get("r", "c", 1) is None and warnings


def test_expired_source_cache_is_rejected_without_increment(env):
    client = MemoryRedis()
    cache = Cache(env.s, client=client)
    result = answer()
    for _ in range(3):
        cache.put("r", "c", 1, "Redis", result)
    key = cache.keys("r", "c", 1)[1]
    payload = json.loads(client.values[key])
    payload["citations"][0]["expires_at"] = 0
    client.values[key] = json.dumps(payload)
    assert cache.get("r", "c", 1) is None
    assert client.hashes[cache.keys("r", "c", 1)[0]]["count"] == 3


def test_difficulty_allocation():
    assert difficulty_targets(InterviewRequest(query="Redis")) == {"基础": 2, "进阶": 2, "高阶": 1}
    assert difficulty_targets(InterviewRequest(
        query="Redis", count=3, difficulties=["高阶"],
    )) == {"高阶": 3}
    assert difficulty_targets(InterviewRequest(query="Redis", count=1))["基础"] == 1


def test_cli_surface_and_json_flag():
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("chat", "ingest", "sync", "doctor", "hot", "sources"):
        assert command in result.stdout
    result = runner.invoke(app, ["chat", "--json"])
    assert result.exit_code == 2


def test_json_output_is_structured_and_history_keeps_question_order(env, monkeypatch):
    import interview_question_bank.cli as cli

    value = answer()
    value.questions = [{"title": "第一题"}, {"title": "第二题"}]
    monkeypatch.setattr(cli, "get_settings", lambda: env.s)
    monkeypatch.setattr(cli, "Assistant", lambda *a, **k: type(
        "Bot", (), {"ask": lambda *a: value}
    )())
    result = CliRunner().invoke(app, ["chat", "-q", "Redis", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["questions"][1]["title"] == "第二题"
    saved = env.store.history(payload["session"])
    assert json.loads(saved[0]["answer"])["questions"][1]["title"] == "第二题"
