from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from dotenv import dotenv_values
from job_agent.config import Settings as JobSettings
from job_agent.service import Service as JobService

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
        return Settings(**options)

    def _assistant(self, kind):
        if kind not in self._assistants:
            if kind == "knowledge":
                from knowledge_assistant.agent import Assistant
            else:
                from interview_question_bank.agent import Assistant
            self._assistants[kind] = Assistant(
                self.knowledge_settings(kind), stage=self.emit, emit=lambda _: None,
            )
        return self._assistants[kind]

    def ask(self, kind, question, history=None, local_only=False):
        from langsmith import tracing_context

        # Child stores bind SQLite connections to their creation thread.
        def invoke():
            with tracing_context(enabled=False):
                return self._assistant(kind).ask(question, history or [], local_only).to_dict()

        return self._worker.submit(invoke).result()

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
