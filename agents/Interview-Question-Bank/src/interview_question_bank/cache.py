import json
import re
import time
import unicodedata

from .models import Answer, InterviewQuestion, digest

PROMPT_VERSION = "interview-v4"
REGISTER = """
local count = redis.call('HINCRBY', KEYS[1], 'count', 1)
if count == 1 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
  redis.call('HSET', KEYS[1], 'question', ARGV[2])
end
if count >= tonumber(ARGV[3]) and tonumber(ARGV[4]) > 0 then
  redis.call('SET', KEYS[2], ARGV[5], 'EX', ARGV[4])
end
return count
"""


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    return " ".join(re.sub(r"[A-Z]", lambda m: m[0].lower(), text).split())


def request_id(question, history, settings, corpus_id, local_only=False):
    # Retain history verbatim: lowercasing source code in prior answers is unsafe.
    return digest([
        normalize(question), history, corpus_id, settings.index_namespace,
        settings.llm_model, settings.llm_base_url, settings.embedding_model,
        PROMPT_VERSION, local_only, settings.retrieval_settings(),
    ])


class Cache:
    def __init__(self, settings, warn=lambda _: None, client=None):
        import redis

        self.s, self.warn = settings, warn
        self.client = client if client is not None else redis.Redis.from_url(
            settings.redis_url.get_secret_value(), decode_responses=True,
            socket_connect_timeout=2, socket_timeout=2,
        )

    def prefix(self, corpus_id):
        return f"iqb:{self.s.index_namespace}:{corpus_id}:"

    def keys(self, rid, corpus_id, version):
        prefix = self.prefix(corpus_id)
        return prefix + "frequency:" + rid, prefix + f"answer:{version}:" + rid

    def get(self, rid, corpus_id, version):
        try:
            counter, key = self.keys(rid, corpus_id, version)
            raw = self.client.get(key)
            if not raw:
                return None
            answer = Answer(**json.loads(raw))
            if not self.eligible(answer):
                return None
            for question in answer.questions:
                InterviewQuestion.model_validate(question)
            answer.route = "cache"
            answer.cache_promoted = True
            # Reuse the atomic fixed-window operation without extending answer TTL.
            answer.hot_count = int(self.client.eval(
                REGISTER, 2, counter, key, self.s.hot_window,
                answer.request.get("query", "缓存请求")[:500],
                self.s.hot_threshold, 0, "",
            ))
            return answer
        except Exception:
            self.warn("Redis 不可用或缓存内容无效；已跳过缓存。")
            return None

    @staticmethod
    def eligible(answer):
        return bool(
            answer.sufficient and answer.persisted and answer.citations
            and all(c["expires_at"] > time.time() for c in answer.citations)
        )

    def put(self, rid, corpus_id, version, question, answer):
        if not self.eligible(answer):
            return
        ttl = min(self.s.cache_ttl, int(min(
            c["expires_at"] for c in answer.citations
        ) - time.time()))
        if ttl < 1:
            return
        try:
            counter, key = self.keys(rid, corpus_id, version)
            count = int(self.client.eval(
                REGISTER, 2, counter, key, self.s.hot_window,
                normalize(question)[:500], self.s.hot_threshold, ttl,
                json.dumps(answer.to_dict(), ensure_ascii=False),
            ))
            answer.hot_count = count
            answer.cache_promoted = count >= self.s.hot_threshold
        except Exception:
            self.warn("Redis 不可用；本次热点与缓存未写入。")

    def hot(self, corpus_id):
        rows = []
        for i, key in enumerate(self.client.scan_iter(
            match=self.prefix(corpus_id) + "frequency:*", count=100
        )):
            if i >= 1000:
                break
            value = self.client.hgetall(key)
            count = int(value.get("count", 0))
            if count >= self.s.hot_threshold:
                rows.append({
                    "question": value.get("question", ""), "count": count,
                    "window_remaining": self.client.ttl(key),
                })
        return sorted(rows, key=lambda row: row["count"], reverse=True)
