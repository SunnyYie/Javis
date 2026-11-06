"""Allowlisted metadata tracing. Never forward LangChain payloads to LangSmith."""

import json
import os
import queue
import threading
import time
from collections import defaultdict
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from functools import wraps
from uuid import uuid4

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.runnables.config import var_child_runnable_config
from langchain_core.tracers.context import register_configure_hook
from langsmith import tracing_context

_recorder = ContextVar("bl_metadata_recorder", default=None)
_parent = ContextVar("bl_metadata_parent", default=None)
_handler = ContextVar("bl_metadata_handler", default=None)
NAMES = frozenset("""
chat command LangGraph model tools cache rewrite retrieve assess acquire recheck generate save
parse online answer verify revise structured llm tool current_state load_skill propose_fact
generate_resume update_preferences search_jobs match_jobs list_jobs select_job job_detail
question_detail generate_questions explain_knowledge knowledge_links start_interview
review_interview learning_plan propose_memory assistant.knowledge assistant.questions
assistant.initialize retrieval embedding vector_search reranker answer_cache job.parse_resume
job.import_file job.search job.match job.export
models.rewrite models.answer models.parse models.assess models.draft models.verify models.structured
/help /consent /status /confirm /resume /import /prefs /contact /facts /draft /adopt /search
/match /jobs /detail /show /target /questions /knowledge /links /learn /interview /answer
/pause /continue /finish /transcript /review /memory /tasks /done /export
""".split())
STATUSES = frozenset(("ok", "partial", "blocked", "failed", "cancelled"))


def _safe_name(name):
    return name if isinstance(name, str) and name in NAMES else "chain"


def result_metrics(value):
    if not isinstance(value, dict):
        return {}
    result = {}
    if isinstance(value.get("status"), str) and value["status"] in STATUSES:
        result["status"] = value["status"]
    for key in ("sufficient", "persisted"):
        if type(value.get(key)) is bool:
            result[key] = value[key]
    for key in ("citations", "questions", "results"):
        if isinstance(value.get(key), list):
            result[key + "_count"] = len(value[key])
    if value.get("sufficient") is False or value.get("persisted") is False:
        result.setdefault("status", "partial")
    return result


def _tokens(response):
    usage = (getattr(response, "llm_output", None) or {}).get("token_usage", {})
    result = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        if type(usage.get(key)) is int and 0 <= usage[key] <= 10**9:
            result[key] = usage[key]
    if not result:
        for group in getattr(response, "generations", []):
            for generation in group:
                message = getattr(generation, "message", None)
                usage = getattr(message, "usage_metadata", None) or {}
                for source, target in (
                    ("input_tokens", "prompt_tokens"), ("output_tokens", "completion_tokens"),
                    ("total_tokens", "total_tokens"),
                ):
                    if type(usage.get(source)) is int and 0 <= usage[source] <= 10**9:
                        result[target] = result.get(target, 0) + usage[source]
    return result


class MetadataHandler(BaseCallbackHandler):
    """Custom callbacks are independent of disabled LangSmith automatic tracing."""

    run_inline = True

    def __init__(self, recorder):
        self.recorder = recorder

    def on_chain_start(self, serialized, inputs, *, run_id, parent_run_id=None, **kwargs):
        name = kwargs.get("name")
        if isinstance(name, str) and name in NAMES:
            self.recorder.start(name, "chain", run_id, parent_run_id)
        else:
            self.recorder.alias(run_id, parent_run_id)

    def on_chain_end(self, outputs, *, run_id, **kwargs):
        self.recorder.end(run_id, result_metrics(outputs))

    def on_chain_error(self, error, *, run_id, **kwargs):
        self.recorder.end(run_id, {"status": "failed"})

    def on_tool_start(self, serialized, input_str, *, run_id, parent_run_id=None, **kwargs):
        self.recorder.start(
            (serialized or {}).get("name"), "tool", run_id, parent_run_id,
        )

    def on_tool_end(self, output, *, run_id, **kwargs):
        # ToolMessage.content is intentionally not parsed or retained.
        self.recorder.end(run_id)

    def on_tool_error(self, error, *, run_id, **kwargs):
        self.recorder.end(run_id, {"status": "failed"})

    def on_chat_model_start(self, serialized, messages, *, run_id, parent_run_id=None, **kw):
        self.recorder.start("llm", "llm", run_id, parent_run_id)

    def on_llm_start(self, serialized, prompts, *, run_id, parent_run_id=None, **kwargs):
        self.recorder.start("llm", "llm", run_id, parent_run_id)

    def on_llm_end(self, response, *, run_id, **kwargs):
        self.recorder.end(run_id, _tokens(response))

    def on_llm_error(self, error, *, run_id, **kwargs):
        self.recorder.end(run_id, {"status": "failed"})


register_configure_hook(_handler, inheritable=True)


class Recorder:
    def __init__(self, name, limit=512):
        self.runs, self.limit, self.dropped = {}, limit, 0
        self.aliases = {}
        self.lock = threading.RLock()
        self.root = self.start(name, "chain")

    def parent(self):
        explicit = _parent.get()
        config = var_child_runnable_config.get() or {}
        manager = config.get("callbacks")
        candidate = self.resolve(getattr(manager, "parent_run_id", None))
        available = [key for key in (explicit, candidate) if key in self.runs]
        return max(available, key=lambda key: len(self.runs[key]["dotted_order"])) \
            if available else None

    def resolve(self, key):
        key = str(key)
        return key if key in self.runs else self.aliases.get(key)

    def alias(self, run_id, parent_id):
        with self.lock:
            if len(self.aliases) < self.limit * 4:
                self.aliases[str(run_id)] = self.resolve(parent_id) or self.parent()

    def start(self, name, run_type, run_id=None, parent_id=None):
        with self.lock:
            key = str(run_id or uuid4())
            if key in self.runs:
                return key
            if len(self.runs) >= self.limit:
                self.dropped += 1
                return None
            choices = [key for key in (self.resolve(parent_id), self.parent()) if key in self.runs]
            parent_id = max(choices, key=lambda key: len(self.runs[key]["dotted_order"])) \
                if choices else None
            now = datetime.now(UTC)
            order = now.strftime("%Y%m%dT%H%M%S%fZ") + key
            if parent_id in self.runs:
                order = self.runs[parent_id]["dotted_order"] + "." + order
            self.runs[key] = {
                "id": key, "parent_run_id": parent_id, "name": _safe_name(name),
                "run_type": run_type, "start_time": now.isoformat(),
                "dotted_order": order, "metrics": {}, "_clock": time.perf_counter(),
            }
            return key

    def end(self, run_id, metrics=None):
        with self.lock:
            run = self.runs.get(str(run_id))
            if run is None or "end_time" in run:
                return
            run["end_time"] = datetime.now(UTC).isoformat()
            run["duration_ms"] = round((time.perf_counter() - run.pop("_clock")) * 1000, 3)
            run["metrics"].update(metrics or {})
            run["metrics"].setdefault("status", "ok")

    def document(self):
        for key in list(self.runs):
            self.end(key, {"status": "cancelled"})
        return {
            "schema": 1, "trace_id": self.root, "privacy": "metadata_only",
            "dropped_spans": self.dropped, "runs": list(self.runs.values()),
        }


@contextmanager
def span(name, run_type="chain"):
    recorder = _recorder.get()
    if recorder is None:
        yield {}
        return
    key = recorder.start(name, run_type)
    token = _parent.set(key or recorder.parent())
    metrics = {}
    try:
        yield metrics
    except BaseException as exc:
        metrics["status"] = "cancelled" if isinstance(exc, KeyboardInterrupt) else "failed"
        raise
    finally:
        recorder.end(key, metrics)
        _parent.reset(token)


def instrument(obj, method, name, run_type="chain", *, cache=False):
    """Instrument only this adapter-owned instance, not shared classes or child CLIs."""
    original = getattr(obj, method, None)
    if not callable(original):
        return

    @wraps(original)
    def call(*args, **kwargs):
        with span(name, run_type) as metrics:
            result = original(*args, **kwargs)
            metrics.update(result_metrics(result))
            if cache:
                hit = getattr(obj, "last_cache_hit", None)
                if type(hit) is bool:
                    metrics["cache_hit"] = hit
                elif name == "answer_cache":
                    metrics["cache_hit"] = result is not None
            return result

    setattr(obj, method, call)


def _write(path, document):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(document, stream, ensure_ascii=False, indent=2)
    temporary.replace(path)


class Telemetry:
    def __init__(self, settings, *, upload_allowed=False, client_factory=None):
        self.settings = settings
        self.mode = settings.trace_mode
        self.upload_allowed = upload_allowed
        self.client_factory = client_factory
        self.last = None
        self._queue = queue.Queue(maxsize=8)
        self._worker = None
        self._closed = False
        self._stop = threading.Event()

    def status(self):
        if self.mode != "langsmith":
            return self.mode
        if not self.upload_allowed:
            return "upload_requires_consent"
        if not self.settings.langsmith_api_key.get_secret_value():
            return "api_key_missing"
        return "ready"

    @contextmanager
    def request(self, name):
        # Even an inherited LANGSMITH_TRACING=true must not upload private payloads.
        with tracing_context(enabled=False):
            if self.mode == "off" or _recorder.get() is not None:
                yield {}
                return
            recorder = Recorder(name)
            rt = _recorder.set(recorder)
            pt = _parent.set(recorder.root)
            ht = _handler.set(MetadataHandler(recorder))
            metrics = {}
            try:
                yield metrics
            except BaseException as exc:
                metrics["status"] = (
                    "cancelled" if isinstance(exc, KeyboardInterrupt) else "failed"
                )
                raise
            finally:
                recorder.end(recorder.root, metrics)
                _handler.reset(ht)
                _parent.reset(pt)
                _recorder.reset(rt)
                self._finish(recorder)

    def _finish(self, recorder):
        document = recorder.document()
        path = self.settings.data_dir / "traces" / f"{recorder.root}.json"
        status = self.status()
        document["upload"] = "queued" if status == "ready" else status
        self.last = {"trace_id": recorder.root, "path": str(path), "upload": document["upload"]}
        try:
            _write(path, document)
            if status == "ready":
                if self._worker is None:
                    self._worker = threading.Thread(
                        target=self._send, name="bl-traces", daemon=True,
                    )
                    self._worker.start()
                try:
                    self._queue.put_nowait((path, document))
                except queue.Full:
                    document["upload"] = "queue_full"
                    _write(path, document)
                    self.last["upload"] = "queue_full"
        except Exception:
            self.last["upload"] = "local_write_failed"

    def _send(self):
        client = None
        try:
            while not self._stop.is_set() or not self._queue.empty():
                try:
                    path, document = self._queue.get(timeout=0.1)
                except queue.Empty:
                    continue
                try:
                    if client is None:
                        from langsmith import Client
                        from urllib3.util import Retry

                        factory = self.client_factory or Client
                        client = factory(
                            api_url=self.settings.langsmith_endpoint,
                            api_key=self.settings.langsmith_api_key.get_secret_value(),
                            auto_batch_tracing=False, timeout_ms=2000,
                            retry_config=Retry(total=0), omit_traced_runtime_info=True,
                            hide_inputs=True, hide_outputs=True, hide_metadata=False,
                            tracing_sampling_rate=1.0, tracing_mode="langsmith",
                            workspace_id=str(self.settings.langsmith_workspace_id)
                            if self.settings.langsmith_workspace_id else "",
                        )
                    runs = []
                    for run in document["runs"]:
                        metrics = run["metrics"]
                        runs.append({
                            **{k: run[k] for k in (
                                "id", "parent_run_id", "name", "run_type", "start_time",
                                "end_time", "dotted_order",
                            )},
                            "trace_id": document["trace_id"],
                            "session_name": self.settings.langsmith_project,
                            "inputs": {}, "outputs": {},
                            "error": "operation_failed"
                            if metrics["status"] in ("failed", "cancelled", "blocked") else None,
                            "extra": {"metadata": {
                                "privacy": "metadata_only", "duration_ms": run["duration_ms"],
                                **metrics,
                            }},
                            "tags": ["brain-leader", "metadata-only"],
                        })
                        if run["run_type"] == "llm":
                            usage = {
                                target: metrics[source] for source, target in (
                                    ("prompt_tokens", "input_tokens"),
                                    ("completion_tokens", "output_tokens"),
                                    ("total_tokens", "total_tokens"),
                                ) if source in metrics
                            }
                            if usage:
                                runs[-1]["extra"]["metadata"]["usage_metadata"] = usage
                    # The high-level batch API suppresses some ingestion errors and retries.
                    # This SDK transport sends one bounded, allowlisted batch, without adding
                    # runtime/environment metadata or silently reporting a failed send as OK.
                    response = client.request_with_retries(
                        "POST", "/runs/batch", request_kwargs={
                            "json": {"post": runs}, "allow_redirects": False,
                        }, stop_after_attempt=1,
                    )
                    if not 200 <= response.status_code < 300:
                        raise RuntimeError("trace_upload_rejected")
                    document["upload"] = "sent_unverified"
                except Exception:
                    document["upload"] = "failed"
                finally:
                    try:
                        _write(path, document)
                    except Exception:
                        pass
                    self._queue.task_done()
        finally:
            if client is not None:
                try:
                    client.close(timeout=0)
                except Exception:
                    pass

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._stop.set()
        if self._worker:
            self._worker.join(timeout=3)


def summarize(document):
    """Inclusive times overlap; do not sum them to estimate end-to-end latency."""
    groups = defaultdict(lambda: {
        "calls": 0, "total_ms": 0, "max_ms": 0, "failures": 0,
        "cache_hits": 0, "cache_misses": 0,
    })
    for run in document["runs"]:
        group = groups[run["name"]]
        group["calls"] += 1
        group["total_ms"] = round(group["total_ms"] + run["duration_ms"], 3)
        group["max_ms"] = max(group["max_ms"], run["duration_ms"])
        group["failures"] += run["metrics"]["status"] in ("failed", "cancelled", "blocked")
        if type(run["metrics"].get("cache_hit")) is bool:
            group["cache_hits"] += run["metrics"]["cache_hit"]
            group["cache_misses"] += not run["metrics"]["cache_hit"]
    llm_runs = [run for run in document["runs"] if run["run_type"] == "llm"]
    return {
        "trace_id": document["trace_id"], "upload": document["upload"],
        "dropped_spans": document["dropped_spans"],
        "llm_runs": len(llm_runs),
        "token_usage_runs": sum("total_tokens" in run["metrics"] for run in llm_runs),
        "inclusive_timings": dict(sorted(
            groups.items(), key=lambda item: item[1]["total_ms"], reverse=True,
        )),
        "tokens": {key: sum(
            run["metrics"].get(key, 0) for run in llm_runs
        ) for key in ("prompt_tokens", "completion_tokens", "total_tokens")},
    }
