import json
import time

from .models import Answer, digest

PROMPT_VERSION = "knowledge-v3"
RETRIEVAL_VERSION = "hybrid-rrf-cross-encoder-v3"


def cache_key(question, history, settings, version, corpus_id=""):
    model = settings.model_settings()
    return "ka:" + digest(
        [
            question.strip(),
            history,
            model["model"],
            model["base_url"],
            settings.embedding_model,
            settings.index_namespace,
            RETRIEVAL_VERSION,
            settings.rerank_enabled,
            settings.reranker_model,
            settings.rerank_candidates,
            settings.readme_policy,
            settings.hybrid_head,
            PROMPT_VERSION,
            version,
            corpus_id,
        ]
    )


class Cache:
    def __init__(self, settings, warn=lambda _: None):
        import redis

        self.client = redis.Redis.from_url(
            settings.redis_url.get_secret_value(),
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        self.s, self.warn = settings, warn

    def get(self, key: str) -> Answer | None:
        try:
            value = self.client.get(key)
            if not value:
                return None
            answer = Answer(**json.loads(value))
            if (
                not answer.sufficient
                or not answer.persisted
                or not answer.citations
                or any(c["expires_at"] <= time.time() for c in answer.citations)
            ):
                return None
            answer.route = "cache"
            return answer
        except Exception:
            self.warn("Redis unavailable or invalid cache entry; cache skipped.")
            return None

    def put(self, key: str, answer: Answer):
        if not answer.persisted or not answer.sufficient or not answer.citations:
            return
        ttl = min(
            self.s.cache_ttl,
            int(min(c["expires_at"] for c in answer.citations) - time.time()),
        )
        if ttl <= 0:
            return
        try:
            self.client.setex(key, ttl, json.dumps(answer.to_dict(), ensure_ascii=False))
        except Exception:
            self.warn("Redis unavailable; answer was not cached.")
