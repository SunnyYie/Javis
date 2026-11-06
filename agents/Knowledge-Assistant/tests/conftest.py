import copy
import time
from types import SimpleNamespace

import pytest
from tokenizers import Tokenizer, models, pre_tokenizers

from knowledge_assistant.config import Settings
from knowledge_assistant.models import Bundle, Document, ServiceError
from knowledge_assistant.retrieval import Knowledge
from knowledge_assistant.storage import Store


@pytest.fixture(autouse=True)
def isolate_unit_environment(request, monkeypatch):
    if request.node.get_closest_marker("live"):
        return
    # pymilvus imports load_dotenv(); neither shell nor local credentials belong in unit tests.
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)


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

    def rewrite(self, question, history):
        self.calls.append("rewrite")
        return f"{history[-1]['question']}: {question}" if history else question

    def assess(self, query, chunks):
        self.calls.append("assess")
        sufficient = bool(chunks) if self.verdict is None else self.verdict
        return SimpleNamespace(sufficient=sufficient, queries=[query])

    def answer(self, query, chunks, sufficient, emit):
        self.calls.append("answer")
        text = self.output or f"{chunks[0].text[:100]} [1]"
        emit(text)
        return text


class FakeCache:
    def __init__(self):
        self.rows = {}

    def get(self, key):
        answer = copy.deepcopy(self.rows.get(key))
        if answer:
            answer.route = "cache"
        return answer

    def put(self, key, answer):
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
        _env_file=None,
        data_dir=tmp_path,
        llm_api_key="test-only",
        embedding_model="fake-model",
        rerank_enabled=False,
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
