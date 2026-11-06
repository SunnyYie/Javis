from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from pathlib import Path

from dotenv import dotenv_values
from job_agent.config import Settings as JobSettings
from job_agent.service import Service as JobService

from .tracing import instrument, result_metrics, span

MODEL_KEYS = ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL", "LLM_TIMEOUT")


def model_options(path: Path) -> dict:
    values = dotenv_values(path)
    return {key.lower(): values[key] for key in MODEL_KEYS if values.get(key)}


class Assistants:
    """Lazy Python adapters. Never invoke a child interactive CLI."""

    def __init__(self, settings, sid, emit=lambda _: None):
        self.settings, self.sid, self.emit = settings, sid, emit
        self._job = None
        self._assistants = {}
        self._worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="bl-corpus")
        self._closed = False

    @property
    def job(self):
        if self._job is None:
            root = self.settings.workspace / "agents" / "Job-Agent"
            s = JobSettings(
                _env_file=root / ".env",
                data_dir=self.settings.data_dir / "jobs",
                **self.settings.model_fields_for_children(),
            )
            self._job = JobService(s, self.sid, emit=self.emit)
            for method in ("parse_resume", "import_file", "search", "match", "export"):
                instrument(self._job, method, "job." + method)
            instrument(self._job.models, "structured", "models.structured")
        return self._job

    def knowledge_settings(self, kind):
        if kind == "knowledge":
            from knowledge_assistant.config import Settings
            name = "Knowledge-Assistant"
            override = self.settings.ka_data_dir
            namespace = self.settings.ka_namespace
        else:
            from interview_question_bank.config import Settings
            name = "Interview-Question-Bank"
            override = self.settings.iqb_data_dir
            namespace = self.settings.iqb_namespace
        root = self.settings.workspace / "agents" / name
        options = dict(
            _env_file=root / ".env", data_dir=override or root / "data",
            model_cache_dir=root / "data" / "models",
            **self.settings.model_fields_for_children(),
        )
        if namespace:
            options["index_namespace"] = namespace
        for field in ("embedding_batch_size", "reranker_batch_size", "inference_cache_size"):
            value = getattr(self.settings, field, None)
            if value is not None:
                options[field] = value
        return Settings(**options)

    def _assistant(self, kind):
        if kind not in self._assistants:
            if kind == "knowledge":
                from knowledge_assistant.agent import Assistant
            else:
                from interview_question_bank.agent import Assistant
            with span("assistant.initialize"):
                child = Assistant(
                    self.knowledge_settings(kind), stage=self.emit, emit=lambda _: None,
                )
            knowledge = getattr(child, "knowledge", None)
            if knowledge is not None:
                instrument(knowledge, "retrieve", "retrieval", "retriever")
                for owner, method, name in (
                    ("embeddings", "query", "embedding"),
                    ("index", "search", "vector_search"),
                    ("reranker", "scores", "reranker"),
                ):
                    obj = getattr(knowledge, owner, None)
                    if obj is not None:
                        instrument(obj, method, name, cache=True)
            cache = getattr(child, "cache", None)
            if cache is not None:
                instrument(cache, "get", "answer_cache", cache=True)
            models = getattr(child, "models", None)
            if models is not None:
                for method in ("rewrite", "answer", "parse", "assess", "draft", "verify"):
                    instrument(models, method, "models." + method)
            self._assistants[kind] = child
        return self._assistants[kind]

    def ask(self, kind, question, history=None, local_only=False):
        from langsmith import tracing_context

        # Child stores bind SQLite connections to their creation thread.
        def invoke():
            with tracing_context(enabled=False), span("assistant." + kind) as metrics:
                result = self._assistant(kind).ask(question, history or [], local_only).to_dict()
                metrics.update(result_metrics(result))
                return result

        return self._worker.submit(copy_context().run, invoke).result()

    def close(self):
        if self._closed:
            return

        def release():
            for assistant in self._assistants.values():
                assistant.store.db.close()
            self._assistants.clear()

        try:
            self._worker.submit(release).result()
        finally:
            self._worker.shutdown(wait=True)
            self._closed = True
