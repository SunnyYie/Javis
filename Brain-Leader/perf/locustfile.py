import json
import os
import time
from pathlib import Path

from locust import HttpUser, constant, events, task

from perf.metrics import summarize

TOPICS = [
    "RAG检索增强生成的基本流程",
    "向量检索和BM25混合检索",
    "Agent工具调用与错误处理",
    "LangGraph状态管理与持久化",
    "Embedding与Reranker的区别",
]
samples = []
started = None
cache_hits = {}


@events.test_start.add_listener
def start(environment, **kwargs):
    global started
    samples.clear()
    cache_hits.clear()
    started = time.perf_counter()


@events.request.add_listener
def record(response_time, exception, context=None, **kwargs):
    samples.append((response_time, exception is not None))
    for key in ("embedding_cache_hits", "reranker_cache_hits"):
        cache_hits[key] = cache_hits.get(key, 0) + (context or {}).get(key, 0)


@events.test_stop.add_listener
def stop(environment, **kwargs):
    elapsed = time.perf_counter() - started
    result = {
        **summarize(samples, elapsed),
        "operation": os.environ["BL_PERF_OPERATION"],
        "kind": os.environ["BL_PERF_KIND"],
        **cache_hits,
    }
    Path(os.environ["BL_PERF_METRICS"]).write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8",
    )


class CareerRetrievalUser(HttpUser):
    wait_time = constant(0)

    def on_start(self):
        self.position = 0

    @task
    def run_operation(self):
        topic = TOPICS[self.position % len(TOPICS)]
        self.position += 1
        operation = os.environ["BL_PERF_OPERATION"]
        with self.client.post(
            "/run", json={"operation": operation, "topic": topic},
            name=operation, catch_response=True, timeout=120,
        ) as response:
            if response.status_code != 200:
                response.failure(f"HTTP {response.status_code}")
                return
            result = response.json()
            response.request_meta["context"].update({
                key: result.get(key, 0)
                for key in ("embedding_cache_hits", "reranker_cache_hits")
            })
            if not result.get("ok") or not result.get("count"):
                response.failure("missing real result")
