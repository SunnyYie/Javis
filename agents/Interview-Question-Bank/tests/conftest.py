import copy
import time
from types import SimpleNamespace

import pytest
from tokenizers import Tokenizer, models, pre_tokenizers

from interview_question_bank.config import Settings
from interview_question_bank.models import (
    Bundle,
    Coverage,
    Document,
    Draft,
    InterviewQuestion,
    InterviewRequest,
    ServiceError,
    Verification,
    difficulty_targets,
)
from interview_question_bank.retrieval import Knowledge
from interview_question_bank.storage import Store


@pytest.fixture(autouse=True)
def isolate_unit_environment(request, monkeypatch, tmp_path):
    if request.node.get_closest_marker("live"):
        return
    # pymilvus imports load_dotenv(); neither shell nor local credentials belong in unit tests.
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "cli"))
    monkeypatch.setenv("LLM_API_KEY", "test-only")
    monkeypatch.setenv("LLM_BASE_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("TAVILY_API_KEY", "")
    monkeypatch.setenv("GITHUB_TOKEN", "")
    monkeypatch.setenv("RERANKER_ENABLED", "false")


class FakeEmbeddings:
    dimension = 3
    fingerprint = "fake-model:3"

    def __init__(self):
        self.calls = []
        self.tokenizer = Tokenizer(models.WordLevel({"[UNK]": 0}, unk_token="[UNK]"))
        self.tokenizer.pre_tokenizer = pre_tokenizers.Split("", behavior="isolated")

    def documents(self, texts):
        self.calls.extend(texts)
        return [[1.0, float(len(t) % 13), 0.5] for t in texts]

    def query(self, text):
        return [1.0, 1.0, 0.5]


class FakeIndex:
    def __init__(self):
        self.rows = {}
        self.searches = 0
        self.available = True
        self.write_failure = False

    def ensure(self):
        if not self.available:
            raise ServiceError("milvus", "unavailable")

    def write(self, chunks, vectors):
        if self.write_failure:
            raise ServiceError("milvus", "write_failed")
        self.rows.update({c.id: v for c, v in zip(chunks, vectors, strict=True)})

    def search(self, vector, active, k=20):
        self.ensure()
        self.searches += 1
        return [cid for cid in active if cid in self.rows][:k]


class FakeModels:
    def __init__(self):
        self.calls = []
        self.verdict = None
        self.output = None

    def parse(self, question, history):
        self.calls.append("parse")
        return InterviewRequest(query=question, topic="LangGraph")

    def assess(self, query, chunks):
        self.calls.append("assess")
        sufficient = bool(chunks) if self.verdict is None else self.verdict
        return Coverage(sufficient=sufficient, queries=["LangGraph interview questions"])

    def draft(self, request, chunks, history, feedback=None):
        self.calls.append("draft")
        if self.output:
            return copy.deepcopy(self.output)
        questions = [
            InterviewQuestion(
                title=f"题目 {difficulty} {i}", difficulty=difficulty,
                knowledge_points=["LangGraph"], answer="文档支持的回答",
                key_points=["关键点"], origin="基于资料生成", evidence_ids=[chunks[0].id],
            )
            for difficulty, count in difficulty_targets(request).items()
            for i in range(count)
        ]
        return Draft(questions=questions)

    def verify(self, request, draft, chunks):
        self.calls.append("verify")
        return Verification(
            sufficient=True, explanation_supported=True,
            supported_question_numbers=list(range(1, len(draft.questions) + 1)),
        )


class FakeCache:
    def __init__(self):
        self.rows = {}

    def get(self, rid, corpus_id, version):
        key = (rid, corpus_id, version)
        answer = copy.deepcopy(self.rows.get(key))
        if answer:
            answer.route = "cache"
        return answer

    def put(self, rid, corpus_id, version, question, answer):
        key = (rid, corpus_id, version)
        if answer.sufficient and answer.persisted and answer.citations:
            self.rows[key] = copy.deepcopy(answer)


class FakeCollectors:
    def __init__(self):
        self.calls = []
        self.bundles = {}
        self.urls = []
        self.github = SimpleNamespace(search=self.search, targeted_paths=lambda *args: [])
        self.web = SimpleNamespace(search=self.search)

    def search(self, query):
        self.calls.append(("search", query))
        return list(self.urls)

    def collect(self, url, **kwargs):
        self.calls.append(("collect", url))
        if url not in self.bundles:
            raise ServiceError("collector", "not_found")
        value = self.bundles[url]
        if isinstance(value, Exception):
            raise value
        return copy.deepcopy(value)


def bundle(
    text="LangGraph provides durable agent execution.",
    *,
    commit="abc",
    paths=None,
    fetched_at=None,
    source="https://github.com/example/project",
):
    docs = [
        Document(
            source=source,
            url=f"{source}/blob/{commit}/{path}",
            text=content,
            path=path,
            repo="example/project",
            commit=commit,
            role="project",
            license="MIT",
            fetched_at=fetched_at or time.time(),
        )
        for path, content in (paths or {"README.md": text}).items()
    ]
    return Bundle(
        source,
        docs,
        {"selected": [d.path for d in docs], "partial": True},
        "example/project",
        commit,
    )


@pytest.fixture
def env(tmp_path):
    s = Settings(
        _env_file=None, data_dir=tmp_path, llm_api_key="test-only", embedding_model="fake-model",
        reranker_enabled=False,
    )
    store = Store(tmp_path)
    embeddings, index = FakeEmbeddings(), FakeIndex()
    knowledge = Knowledge(s, store, embeddings, index)
    return SimpleNamespace(
        s=s,
        store=store,
        embeddings=embeddings,
        index=index,
        knowledge=knowledge,
        models=FakeModels(),
        collectors=FakeCollectors(),
        cache=FakeCache(),
    )
