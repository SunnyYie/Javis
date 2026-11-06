import json

import redis

from .models import digest


class Cache:
    def __init__(self, settings, sid: str):
        scope = digest(str(settings.data_dir.expanduser().resolve()))[:20]
        self.prefix = f"job-agent:v2:{scope}:{sid}:"
        self.ttl = settings.cache_ttl
        self.warnings: set[str] = set()
        self.client = redis.Redis.from_url(
            settings.redis_url.get_secret_value(),
            socket_timeout=1, socket_connect_timeout=1, decode_responses=True,
        )

    def key(self, kind: str, value) -> str:
        return self.prefix + kind + ":" + digest(value)

    def get(self, key: str):
        try:
            value = self.client.get(key)
            return json.loads(value) if value else None
        except (redis.RedisError, ValueError):
            self.warnings.add("Redis 不可用或缓存损坏，本次跳过缓存")
            return None

    def set(self, key: str, value):
        try:
            self.client.setex(key, self.ttl, json.dumps(value, ensure_ascii=False))
        except redis.RedisError:
            self.warnings.add("Redis 不可用，本次结果未缓存")

    def purge(self):
        # Deletion must fail visibly if private cache records could remain.
        keys = list(self.client.scan_iter(match=self.prefix + "*", count=100))
        if keys:
            self.client.delete(*keys)

    def ping(self) -> bool:
        try:
            return bool(self.client.ping())
        except redis.RedisError:
            return False
