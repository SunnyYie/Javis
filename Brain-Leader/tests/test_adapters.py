import os
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from javis_tools.adapters import Assistants

from brain_leader.config import Settings


def test_child_settings_keep_isolated_paths_and_do_not_mutate_environment(tmp_path):
    settings = Settings(
        _env_file=None, llm_env_file=tmp_path / "absent.env",
        llm_api_key="test-not-a-real-key", llm_model="test-structured-model",
        llm_timeout=37, data_dir=tmp_path / "runtime",
        ka_data_dir=tmp_path / "ka", ka_namespace="blka_unit",
        iqb_data_dir=tmp_path / "iqb", iqb_namespace="bliqb_unit",
    )
    before = dict(os.environ)
    adapters = Assistants(settings, "adapter-unit")
    for kind, path, namespace, name in (
        ("knowledge", settings.ka_data_dir, "blka_unit", "Knowledge-Assistant"),
        ("questions", settings.iqb_data_dir, "bliqb_unit", "Interview-Question-Bank"),
    ):
        child = adapters.knowledge_settings(kind)
        assert child.data_dir == path
        assert child.index_namespace == namespace
        assert child.llm_model == "test-structured-model"
        assert child.llm_api_key.get_secret_value() == "test-not-a-real-key"
        assert child.llm_timeout == 37
        assert child.model_cache_dir == settings.workspace / "agents" / name / "data" / "models"
    assert adapters.job.settings.data_dir == settings.data_dir / "jobs"
    assert dict(os.environ) == before
    adapters.close()


def test_sqlite_child_lifecycle_stays_on_owner_thread(tmp_path, monkeypatch):
    from knowledge_assistant import agent

    threads = []

    class Child:
        def __init__(self, *args, **kwargs):
            threads.append(threading.get_ident())
            self.store = SimpleNamespace(db=sqlite3.connect(":memory:"))

        def ask(self, *args):
            threads.append(threading.get_ident())
            self.store.db.execute("SELECT 1").fetchone()
            return SimpleNamespace(to_dict=lambda: {"sufficient": True})

    monkeypatch.setattr(agent, "Assistant", Child)
    adapters = Assistants(Settings(_env_file=None, data_dir=tmp_path), "thread-unit")
    with ThreadPoolExecutor(max_workers=2) as callers:
        futures = [callers.submit(adapters.ask, "knowledge", "test") for _ in range(2)]
        assert all(f.result()["sufficient"] for f in futures)
    adapters.close()
    adapters.close()
    assert len(threads) == 3 and len(set(threads)) == 1
    assert threads[0] != threading.get_ident()
