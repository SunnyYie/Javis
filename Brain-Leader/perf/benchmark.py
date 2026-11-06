"""Real isolated retrieval behind a serial owner thread and loopback load adapter."""
import argparse
import cProfile
import importlib
import json
import os
import platform
import pstats
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from javis_tools.adapters import Assistants
from javis_tools.isolation import clone_corpus

from brain_leader.config import ROOT, Settings

TOPICS = [
    "RAG检索增强生成的基本流程", "向量检索和BM25混合检索",
    "Agent工具调用与错误处理", "LangGraph状态管理与持久化",
    "Embedding与Reranker的区别",
]


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    path.chmod(0o600)


def source_state(manifests):
    result = {}
    for kind, item in manifests.items():
        with sqlite3.connect(Path(item["source"]).as_uri() + "?mode=ro", uri=True) as db:
            meta = dict(db.execute("SELECT key,value FROM meta"))
        result[kind] = {key: meta[key] for key in ("corpus_id", "version")}
    return result


def prepare(root):
    root.mkdir(parents=True, exist_ok=False, mode=0o700)
    adapters = Assistants(Settings(), "performance-isolation")
    try:
        manifests = {
            kind: clone_corpus(adapters.knowledge_settings(kind), root / "corpora" / kind, kind)
            for kind in ("knowledge", "questions")
        }
        write(root / "manifest.json", manifests)
        write(root / "production-before.json", source_state(manifests))
    finally:
        adapters.close()


def settings_for(root):
    manifests = json.loads((root / "manifest.json").read_text())
    for kind, prefix in (("knowledge", "blka_"), ("questions", "bliqb_")):
        item = manifests[kind]
        if not item["namespace"].startswith(prefix):
            raise ValueError("Refusing production namespace")
        if Path(item["data_dir"]).resolve() == Path(item["source"]).parent.resolve():
            raise ValueError("Refusing production data directory")
        if not (Path(item["data_dir"]) / "isolation.json").is_file():
            raise ValueError("Missing isolation manifest")
    return Settings(
        data_dir=root / "runtime", memory_dir=root / "memory", local_only=True,
        ka_data_dir=Path(manifests["knowledge"]["data_dir"]),
        ka_namespace=manifests["knowledge"]["namespace"],
        iqb_data_dir=Path(manifests["questions"]["data_dir"]),
        iqb_namespace=manifests["questions"]["namespace"],
    )


class Workload:
    def __init__(self, root, kind):
        self.kind = kind
        self.adapters = Assistants(settings_for(root), "performance")

    def submit(self, operation, topic=TOPICS[0]):
        return self.adapters._worker.submit(self.perform, operation, topic).result(timeout=120)

    def perform(self, operation, topic):
        assistant = self.adapters._assistant(self.kind)
        if operation == "snapshot":
            chunks = assistant.store.active_chunks()
            return {"ok": True, "count": len(chunks)}
        if operation in ("retrieve", "retrieve_uncached"):
            if operation == "retrieve_uncached":
                self.clear_inference_cache(assistant.knowledge)
            chunks = assistant.knowledge.retrieve(topic)
            trace = assistant.knowledge.last_trace
            rows = trace if isinstance(trace, list) else [trace]
            return {
                "ok": True, "count": len(chunks), "ids": [c.id for c in chunks],
                "embedding_cache_hits": sum(bool(r.get("embedding_cache_hit")) for r in rows),
                "reranker_cache_hits": sum(bool(r.get("reranker_cache_hit")) for r in rows),
            }
        if operation == "answer":
            answer = assistant.ask(topic, [], local_only=True)
            return {
                "ok": answer.sufficient and answer.persisted,
                "count": len(answer.citations), "route": answer.route,
            }
        raise ValueError("Unknown benchmark operation")

    @staticmethod
    def clear_inference_cache(knowledge):
        for component, name in (
            (knowledge.embeddings, "_query_cache"), (knowledge.reranker, "_score_cache"),
        ):
            cache = getattr(component, name, None)
            if cache is not None:
                cache.clear()

    def profile(self, output):
        def run():
            assistant = self.adapters._assistant(self.kind)
            profiler = cProfile.Profile()
            profiler.enable()
            snapshots = [self.perform("snapshot", "") for _ in range(50)]
            profiler.disable()
            profiler.dump_stats(str(output / "snapshot.prof"))
            profiler = cProfile.Profile()
            self.clear_inference_cache(assistant.knowledge)
            profiler.enable()
            retrieval = {topic: self.perform("retrieve", topic) for topic in TOPICS}
            profiler.disable()
            profiler.dump_stats(str(output / "warm.prof"))
            with (output / "profile.txt").open("w") as stream:
                pstats.Stats(profiler, stream=stream).strip_dirs().sort_stats(
                    "cumulative",
                ).print_stats(45)
            chunks = assistant.store.active_chunks()
            timings = {}
            for name, fn in (
                ("embedding_16", lambda: assistant.knowledge.embeddings.documents(
                    [c.text for c in chunks[:16]],
                )),
                ("rerank_16", lambda: assistant.knowledge.reranker.scores(TOPICS[0], chunks[:16])),
            ):
                start = time.perf_counter()
                value = fn()
                timings[name] = {"seconds": time.perf_counter() - start, "count": len(value)}
            return {"snapshots": snapshots[:1], "retrieval": retrieval, "components": timings}
        return self.adapters._worker.submit(run).result(timeout=600)

    def tune(self):
        def run():
            import numpy as np

            assistant = self.adapters._assistant(self.kind)
            knowledge = assistant.knowledge
            knowledge.retrieve(TOPICS[0])
            chunks = assistant.store.active_chunks()
            trace = knowledge.last_trace
            ids = trace[-1]["rrf"] if isinstance(trace, list) else trace["fused_ids"]
            by_id = {c.id: c for c in chunks}
            candidates = [by_id[cid] for cid in ids][:16]
            model = knowledge.reranker
            encoder = model.model if self.kind == "knowledge" else model._model
            texts = [
                f"{c.repo or c.source} {c.path}\n{c.text}" if self.kind == "knowledge" else c.text
                for c in candidates
            ]
            tokenizer = encoder.model.tokenizer
            lengths = [len(tokenizer.encode(TOPICS[0], text).ids) for text in texts]
            order = sorted(range(len(texts)), key=lambda i: lengths[i])
            rows, reference = [], None
            for batch_size, bucketed in ((4, False), (8, False), (16, False), (4, True)):
                sequence = order if bucketed else list(range(len(texts)))
                start = time.perf_counter()
                scores = list(encoder.rerank(
                    TOPICS[0], [texts[i] for i in sequence], batch_size=batch_size,
                ))
                elapsed = time.perf_counter() - start
                restored = [0.] * len(scores)
                for index, score in zip(sequence, scores, strict=True):
                    restored[index] = float(score)
                if reference is None:
                    reference = restored
                rows.append({
                    "batch_size": batch_size, "length_bucketed": bucketed,
                    "seconds": elapsed, "documents": len(texts),
                    "max_score_delta": float(np.max(np.abs(np.array(restored) - reference))),
                    "same_order": np.argsort(-np.array(restored)).tolist() == np.argsort(
                        -np.array(reference),
                    ).tolist(),
                })
            embeddings = []
            source_texts = [c.text for c in chunks[:16]]
            vectors = None
            for batch_size in (8, 16, 32):
                start = time.perf_counter()
                values = list(knowledge.embeddings.model.embed(
                    source_texts, batch_size=batch_size,
                ))
                elapsed = time.perf_counter() - start
                if vectors is None:
                    vectors = values
                embeddings.append({
                    "batch_size": batch_size, "seconds": elapsed, "documents": len(values),
                    "max_vector_delta": float(np.max(np.abs(np.array(values) - vectors))),
                })
            return {"reranker": rows, "pair_tokens": lengths, "embedding": embeddings}
        return self.adapters._worker.submit(run).result(timeout=600)

    def close(self):
        self.adapters.close()


def serve(root, output, kind):
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    workload = Workload(root, kind)
    start = time.perf_counter()
    try:
        first = workload.submit("retrieve")
        write(output / "cold.json", {
            "seconds": time.perf_counter() - start, "result": first,
            "includes": "assistant/model initialization and first real retrieval; disk cache retained",
        })

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    if self.path != "/run" or not 0 < size <= 4096:
                        raise ValueError("invalid benchmark request")
                    body = json.loads(self.rfile.read(size))
                    if body["operation"] not in {"snapshot", "retrieve", "retrieve_uncached"}:
                        raise ValueError("Only local retrieval operations may be load tested")
                    result = workload.submit(body["operation"], body.get("topic", TOPICS[0]))
                    self.send_response(200)
                except Exception as exc:
                    result = {"ok": False, "error": type(exc).__name__}
                    self.send_response(500)
                payload = json.dumps(result).encode()
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                try:
                    self.wfile.write(payload)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def do_GET(self):
                try:
                    if self.path == "/profile":
                        result = workload.profile(output)
                        write(output / "profile-results.json", result)
                    elif self.path == "/ready":
                        result = {"ok": True}
                    else:
                        raise ValueError("unknown endpoint")
                    payload = json.dumps(result).encode()
                    self.send_response(200)
                except Exception as exc:
                    payload = json.dumps({"error": type(exc).__name__}).encode()
                    self.send_response(500)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
            signal.signal(signal.SIGTERM, lambda *_: threading.Thread(
                target=server.shutdown, daemon=True,
            ).start())
            write(output / "ready.json", {"port": server.server_port, "pid": os.getpid()})
            server.serve_forever(poll_interval=.1)
    finally:
        workload.close()


def measure(root, phase, kind, duration, users, operations):
    output = root / phase / kind
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise ValueError("Refusing to overwrite results")
    log_path = output.parent / f"{kind}-server.log"
    with log_path.open("w") as log:
        server = subprocess.Popen(
            [sys.executable, "-m", "perf.benchmark", "serve", str(root),
             "--phase", phase, "--kind", kind], stdout=log, stderr=log,
        )
        try:
            deadline = time.monotonic() + 240
            while not (output / "ready.json").exists():
                if server.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError(f"Backend failed; inspect {log_path}")
                time.sleep(.2)
            port = json.loads((output / "ready.json").read_text())["port"]
            host = f"http://127.0.0.1:{port}"
            with urllib.request.urlopen(host + "/profile", timeout=600) as response:
                assert response.status == 200
            write(output / "environment.json", {
                "platform": platform.platform(), "python": platform.python_version(),
                "cpu_count": os.cpu_count(), "users": users, "duration_seconds": duration,
                "operations": operations, "single_owner_thread": True,
                "remote_llm_under_load": False, "answer_cache_in_retrieval": False,
                "git_revision": subprocess.check_output(
                    ["git", "rev-parse", "HEAD"], text=True,
                ).strip(),
                "source_diff": subprocess.check_output(
                    ["git", "diff", "--", "../agents", "../tools", "src"], text=True,
                ),
            })
            for operation in operations:
                for count in users:
                    stem = output / f"{operation}-u{count}"
                    env = {
                        **os.environ, "BL_PERF_OPERATION": operation, "BL_PERF_KIND": kind,
                        "BL_PERF_METRICS": str(stem.with_suffix(".json")),
                    }
                    subprocess.run(
                        [sys.executable, "-m", "locust", "-f", str(ROOT / "perf/locustfile.py"),
                         "--headless", "--only-summary", "--host", host, "--users", str(count),
                         "--spawn-rate", str(count), "--run-time", f"{duration}s",
                         "--stop-timeout", "120", "--csv", str(stem),
                         "--exit-code-on-error", "1"],
                        env=env, check=True, timeout=duration + 180,
                    )
        finally:
            server.terminate()
            try:
                server.wait(timeout=150)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()
            manifests = json.loads((root / "manifest.json").read_text())
            after = source_state(manifests)
            write(output / "production-after.json", after)
            if after != json.loads((root / "production-before.json").read_text()):
                raise RuntimeError("Production corpus changed during benchmark")


def profile_direct(root, phase, kind):
    """Profile on the calling thread, without HTTP polling or owner-queue wait time."""
    output = root / phase / f"{kind}-direct-profile"
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    adapters = Assistants(settings_for(root), "performance-profile")
    package = "knowledge_assistant" if kind == "knowledge" else "interview_question_bank"
    assistant = importlib.import_module(f"{package}.agent").Assistant(
        adapters.knowledge_settings(kind),
    )
    try:
        start = time.perf_counter()
        assistant.knowledge.retrieve(TOPICS[0])
        cold = time.perf_counter() - start
        Workload.clear_inference_cache(assistant.knowledge)
        profiler = cProfile.Profile()

        def run():
            return {
                topic: [c.id for c in assistant.knowledge.retrieve(topic)]
                for topic in TOPICS[:2]
            }

        result = profiler.runcall(run)
        profiler.dump_stats(str(output / "retrieval.prof"))
        with (output / "profile.txt").open("w") as stream:
            pstats.Stats(profiler, stream=stream).sort_stats("cumulative").print_stats(40)
        snapshot = cProfile.Profile()

        def read_snapshots():
            for _ in range(50):
                assistant.store.active_chunks()

        snapshot.runcall(read_snapshots)
        snapshot.dump_stats(str(output / "snapshot.prof"))
        write(output / "results.json", {
            "initial_retrieval_seconds": cold, "retrieval_ids": result,
            "profiled_queries": 2, "http_server_running": False,
        })
    finally:
        assistant.store.db.close()
        assistant.cache.client.close()
        adapters.close()


def smoke(root, phase):
    """One paid supervisor request; never part of unbounded Locust traffic."""
    from knowledge_assistant.cache import cache_key

    from brain_leader.agent import chat_turn
    from brain_leader.service import Service

    output = root / phase / "supervisor-smoke.json"
    if output.exists():
        raise ValueError("Refusing to overwrite supervisor smoke evidence")
    service = Service(settings_for(root), f"performance-{phase}")
    question = "langchain-ai/langchain 的README列出哪些生态组件？"
    service.consent()
    try:
        def clear_owned_answer():
            assistant = service.adapters._assistant("knowledge")
            key = cache_key(
                question, [], assistant.s, assistant.store.version, assistant.store.corpus_id,
            )
            service.adapters._assistant("knowledge").cache.client.delete(key)

        service.adapters._worker.submit(clear_owned_answer).result(timeout=60)
        start = time.perf_counter()
        result = chat_turn(
            service, f"先查看状态，然后明确调用一次 explain_knowledge，问题为“{question}”。"
            "只做这次知识查询，不修改条件、不创建其他任务。",
        )
        elapsed = time.perf_counter() - start
        records = service.store.records(service.sid, "knowledge")
        write(output, {
            "seconds": elapsed, "samples": 1, "p99": None,
            "operations": result.get("operations", []),
            "sources_sufficient": bool(records and records[-1]["sufficient"]),
            "persisted": bool(records and records[-1]["persisted"]),
            "citations": len(records[-1]["citations"]) if records else 0,
            "knowledge_route": records[-1].get("route") if records else None,
            "knowledge_records": len(records),
            "answer_cache_cleared_for_exact_owned_key": True,
            "not_a_load_or_tail_latency_measurement": True,
        })
        if not (
            len(records) == 1 and records[-1]["sufficient"] and records[-1]["persisted"]
            and records[-1]["citations"] and records[-1].get("route") != "cache"
        ):
            raise RuntimeError("Supervisor smoke failed or reused a complete cached answer")
    finally:
        service.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=["prepare", "serve", "measure", "tune", "profile", "smoke"],
    )
    parser.add_argument("root", type=Path)
    parser.add_argument("--phase", default="baseline")
    parser.add_argument("--kind", choices=["knowledge", "questions"], default="knowledge")
    parser.add_argument("--duration", type=int, default=30)
    parser.add_argument("--users", type=int, nargs="+", default=[1, 4])
    parser.add_argument("--operations", nargs="+",
                        choices=["snapshot", "retrieve", "retrieve_uncached"],
                        default=["snapshot", "retrieve"])
    args = parser.parse_args()
    root = args.root.resolve()
    if args.action == "prepare":
        prepare(root)
    elif args.action == "serve":
        serve(root, root / args.phase / args.kind, args.kind)
    elif args.action == "tune":
        workload = Workload(root, args.kind)
        try:
            write(root / f"tuning-{args.kind}.json", workload.tune())
        finally:
            workload.close()
    elif args.action == "profile":
        profile_direct(root, args.phase, args.kind)
    elif args.action == "smoke":
        smoke(root, args.phase)
    else:
        if args.duration < 5 or any(not 1 <= n <= 8 for n in args.users):
            parser.error("Use duration >= 5 and 1-8 users; preserve serial ownership")
        measure(root, args.phase, args.kind, args.duration, args.users, args.operations)


if __name__ == "__main__":
    main()
