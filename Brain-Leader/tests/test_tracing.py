import json
import os
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from javis_tools.adapters import Assistants
from javis_tools.tracing import MetadataHandler, Recorder, Telemetry, span, summarize
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult
from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from brain_leader.cli import run
from brain_leader.config import Settings

SECRET = "PRIVATE_RESUME_13800138000_secret@example.test"


def settings(tmp_path, **kwargs):
    return Settings(
        _env_file=None, data_dir=tmp_path / "runtime", memory_dir=tmp_path / "memory",
        llm_env_file=tmp_path / "absent.env", **kwargs,
    )


def document(telemetry):
    return json.loads(Path(telemetry.last["path"]).read_text(encoding="utf-8"))


def test_default_off_ignores_global_enable_and_does_not_create_traces(
    service, monkeypatch,
):
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "unrelated-global-secret")
    import langsmith.client

    def forbidden(*args, **kwargs):
        pytest.fail("default-off must not construct a LangSmith client")

    monkeypatch.setattr(langsmith.client, "Client", forbidden)
    before = dict(os.environ)
    assert run(service, "/status")["status"] == "ok"
    with service.telemetry.request("chat"):
        assert FakeListChatModel(responses=[SECRET]).invoke(SECRET).content == SECRET
    assert dict(os.environ) == before
    assert not (service.settings.data_dir / "traces").exists()


@pytest.mark.parametrize(
    ("allowed", "key", "status"),
    [(False, "dedicated", "upload_requires_consent"), (True, "", "api_key_missing")],
)
def test_upload_requires_dedicated_key_and_separate_consent(tmp_path, allowed, key, status):
    def forbidden(**kwargs):
        pytest.fail("must not construct a client")

    telemetry = Telemetry(
        settings(tmp_path, trace_mode="langsmith", langsmith_api_key=key),
        upload_allowed=allowed, client_factory=forbidden,
    )
    with telemetry.request("chat"):
        pass
    telemetry.close()
    assert document(telemetry)["upload"] == status
    assert telemetry._worker is None


def test_local_metadata_never_contains_payloads_or_raw_errors(consented):
    consented.telemetry.mode = "local"
    result = run(consented, f"/facts add skills {SECRET}")
    assert result["status"] == "ok"
    saved = document(consented.telemetry)
    assert saved["upload"] == "local"
    assert saved["runs"][0]["name"] == "/facts"
    assert SECRET not in json.dumps(saved)
    assert consented.sid not in json.dumps(saved)
    assert os.stat(result["trace"]["path"]).st_mode & 0o777 == 0o600
    with pytest.raises(RuntimeError):
        with consented.telemetry.request(SECRET):
            with span(SECRET):
                raise RuntimeError(SECRET)
    saved = document(consented.telemetry)
    assert SECRET not in json.dumps(saved)
    assert all(r["metrics"]["status"] == "failed" for r in saved["runs"])


def test_sdk_transport_uses_only_allowlisted_payload_and_explicit_endpoint(tmp_path, monkeypatch):
    import requests

    sent = []

    def request(session, method, url, **kwargs):
        sent.append((method, url, kwargs))
        response = requests.Response()
        response.status_code = 200
        response._content = b"{}"
        return response

    monkeypatch.setattr(requests.Session, "request", request)
    monkeypatch.setenv("LANGSMITH_API_KEY", "wrong-global-key")
    monkeypatch.setenv("LANGSMITH_PROJECT", SECRET)
    monkeypatch.setenv("LANGSMITH_WORKSPACE_ID", "unrelated-workspace")
    monkeypatch.setenv("LANGSMITH_METADATA", '{"private": "' + SECRET + '"}')
    telemetry = Telemetry(
        settings(tmp_path, trace_mode="langsmith", langsmith_api_key="dedicated-test-key",
                 langsmith_endpoint="http://127.0.0.1:39999",
                 langsmith_project="trace-test"),
        upload_allowed=True,
    )
    with telemetry.request("chat"):
        with span("reranker") as metrics:
            metrics["cache_hit"] = True
        with span("llm", "llm") as metrics:
            metrics.update(prompt_tokens=10, completion_tokens=5, total_tokens=15)
    telemetry.close()
    assert document(telemetry)["upload"] == "sent_unverified"
    assert len(sent) == 1
    method, url, kwargs = sent[0]
    assert (method, url) == ("POST", "http://127.0.0.1:39999/runs/batch")
    assert kwargs["headers"]["x-api-key"] == "dedicated-test-key"
    assert kwargs["allow_redirects"] is False
    assert "x-tenant-id" not in {key.lower() for key in kwargs["headers"]}
    body = kwargs["json"]
    assert SECRET not in json.dumps(body)
    assert "dedicated-test-key" not in json.dumps(body)
    assert "wrong-global-key" not in json.dumps(body)
    assert len(body["post"]) == 3
    for item in body["post"]:
        assert item["inputs"] == item["outputs"] == {}
        assert item["session_name"] == "trace-test"
        assert "runtime" not in item["extra"]
    assert body["post"][1]["parent_run_id"] == body["post"][0]["id"]
    assert body["post"][1]["extra"]["metadata"]["cache_hit"] is True
    assert body["post"][2]["extra"]["metadata"]["usage_metadata"] == {
        "input_tokens": 10, "output_tokens": 5, "total_tokens": 15,
    }


@pytest.mark.parametrize("status", [401, 429, 500, 302])
def test_failed_upload_does_not_break_business_or_silently_succeed(
    tmp_path, monkeypatch, status,
):
    import requests

    calls = []

    def request(*args, **kwargs):
        calls.append(1)
        response = requests.Response()
        response.status_code = status
        response._content = b'{"error":"private-server-error"}'
        response.url = "http://127.0.0.1:39999/runs/batch"
        return response

    monkeypatch.setattr(requests.Session, "request", request)
    telemetry = Telemetry(
        settings(tmp_path, trace_mode="langsmith", langsmith_api_key="test-only",
                 langsmith_endpoint="http://127.0.0.1:39999"),
        upload_allowed=True,
    )
    with telemetry.request("chat"):
        value = 42
    telemetry.close()
    assert value == 42 and len(calls) == 1
    saved = document(telemetry)
    assert saved["upload"] == "failed"
    assert "private-server-error" not in json.dumps(saved)


def test_worker_preserves_graph_model_and_inference_parentage(tmp_path, monkeypatch):
    from knowledge_assistant import agent

    class Child:
        def __init__(self, *args, **kwargs):
            self.store = SimpleNamespace(db=sqlite3.connect(":memory:"))
            self.knowledge = SimpleNamespace(
                embeddings=SimpleNamespace(query=lambda text: [1.0], last_cache_hit=True),
                index=SimpleNamespace(search=lambda vector: [SECRET]),
                reranker=SimpleNamespace(scores=lambda text: [0.9], last_cache_hit=False),
            )

            def retrieve(text):
                self.knowledge.embeddings.query(text)
                self.knowledge.index.search([1.0])
                self.knowledge.reranker.scores(text)
                return [SECRET]

            self.knowledge.retrieve = retrieve
            self.model = FakeListChatModel(responses=[SECRET])
            graph = StateGraph(dict)

            def retrieve_node(state):
                self.knowledge.retrieve(state["question"])
                return state

            def generate(state):
                self.model.invoke(state["question"])
                return state

            graph.add_node("retrieve", retrieve_node)
            graph.add_node("generate", generate)
            graph.add_edge(START, "retrieve")
            graph.add_edge("retrieve", "generate")
            graph.add_edge("generate", END)
            self.graph = graph.compile()

        def ask(self, question, *args):
            self.graph.invoke({"question": question})
            return SimpleNamespace(to_dict=lambda: {"sufficient": True, "text": SECRET})

    monkeypatch.setattr(agent, "Assistant", Child)
    config = settings(tmp_path, trace_mode="local")
    adapters = Assistants(config, SECRET)
    telemetry = Telemetry(config)
    with telemetry.request("chat"):
        with span("tool", "tool"):
            assert adapters.ask("knowledge", SECRET)["text"] == SECRET
    adapters.close()
    telemetry.close()
    saved = document(telemetry)
    assert SECRET not in json.dumps(saved)
    by_name = {r["name"]: r for r in saved["runs"]}
    by_id = {r["id"]: r for r in saved["runs"]}
    for name in ("assistant.knowledge", "retrieval", "embedding", "vector_search", "reranker",
                 "llm", "retrieve", "generate"):
        assert name in by_name, saved
    assert by_name["assistant.knowledge"]["parent_run_id"] == by_name["tool"]["id"]
    assert by_name["retrieval"]["parent_run_id"] == by_name["retrieve"]["id"]
    assert by_name["embedding"]["parent_run_id"] == by_name["retrieval"]["id"]
    assert by_name["llm"]["parent_run_id"] == by_name["generate"]["id"]
    assert by_name["embedding"]["metrics"]["cache_hit"] is True
    assert by_name["reranker"]["metrics"]["cache_hit"] is False
    assert all(not r["parent_run_id"] or r["parent_run_id"] in by_id for r in saved["runs"])


def test_token_usage_kept_but_model_text_and_metadata_discarded():
    recorder = Recorder("chat")
    handler = MetadataHandler(recorder)
    run_id = uuid4()
    handler.on_chat_model_start(
        {"secret": SECRET}, [[SECRET]], run_id=run_id, metadata={"secret": SECRET},
    )
    handler.on_llm_end(LLMResult(generations=[[ChatGeneration(message=AIMessage(
        content=SECRET, usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
    ))]]), run_id=run_id)
    saved = recorder.document()
    saved["upload"] = "local"
    assert SECRET not in json.dumps(saved)
    assert summarize(saved)["tokens"] == {
        "prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15,
    }


def test_cancellation_and_span_budget_are_persisted(tmp_path):
    telemetry = Telemetry(settings(tmp_path, trace_mode="local"))
    with pytest.raises(KeyboardInterrupt):
        with telemetry.request("chat"):
            raise KeyboardInterrupt(SECRET)
    assert document(telemetry)["runs"][0]["metrics"]["status"] == "cancelled"
    recorder = Recorder("chat", limit=2)
    recorder.start("llm", "llm")
    assert recorder.start("llm", "llm") is None
    assert recorder.document()["dropped_spans"] == 1
    telemetry.close()


def test_parser_aliases_preserve_meaningful_parent_without_logging_parser_name():
    recorder = Recorder("chat")
    handler = MetadataHandler(recorder)
    parser_id, model_id = uuid4(), uuid4()
    handler.on_chain_start({}, {"private": SECRET}, name=SECRET, run_id=parser_id,
                           parent_run_id=recorder.root)
    handler.on_chat_model_start({}, [[SECRET]], run_id=model_id, parent_run_id=parser_id)
    handler.on_llm_error(RuntimeError(SECRET), run_id=model_id)
    saved = recorder.document()
    assert len(saved["runs"]) == 2
    assert saved["runs"][1]["parent_run_id"] == recorder.root
    assert saved["runs"][1]["metrics"]["status"] == "failed"
    assert SECRET not in json.dumps(saved)


def test_missing_usage_is_not_reported_as_fully_measured_zero_tokens():
    recorder = Recorder("chat")
    handler = MetadataHandler(recorder)
    run_id = uuid4()
    handler.on_chat_model_start({}, [[SECRET]], run_id=run_id)
    handler.on_llm_end(LLMResult(generations=[]), run_id=run_id)
    saved = recorder.document()
    saved["upload"] = "local"
    report = summarize(saved)
    assert report["llm_runs"] == 1
    assert report["token_usage_runs"] == 0


def test_local_write_failure_does_not_break_command(service, monkeypatch):
    import javis_tools.tracing

    def fail(*args):
        raise OSError(SECRET)

    monkeypatch.setattr(javis_tools.tracing, "_write", fail)
    service.telemetry.mode = "local"
    result = run(service, "/status")
    assert result["status"] == "ok"
    assert result["trace"]["upload"] == "local_write_failed"
    assert SECRET not in json.dumps(result["trace"])


@pytest.mark.parametrize("endpoint", [
    "http://example.com", "https://user:password@example.com",
    "https://example.com?key=secret", "https://example.com#secret",
])
def test_endpoint_validation_rejects_credentials_and_insecure_remote(tmp_path, endpoint):
    with pytest.raises(ValidationError):
        settings(tmp_path, langsmith_endpoint=endpoint)
