import json
import re
import time
from typing import TypedDict
from urllib.parse import urlencode

from langgraph.graph import END, START, StateGraph

from .cache import Cache, cache_key
from .chunking import Chunker
from .collectors import Collectors
from .llm import Models
from .models import Answer, Chunk, ServiceError
from .retrieval import Knowledge
from .storage import Store


class State(TypedDict, total=False):
    question: str
    query: str
    history: list[dict]
    local_only: bool
    chunks: list[Chunk]
    sufficient: bool
    queries: list[str]
    answer: Answer
    warnings: list[str]
    transient: list[Chunk]
    online: bool


class Assistant:
    def __init__(
        self,
        settings,
        *,
        store=None,
        knowledge=None,
        models=None,
        collectors=None,
        cache=None,
        stage=lambda _: None,
        emit=lambda _: None,
    ):
        self.s = settings
        self.store = store or Store(settings.data_dir)
        self.knowledge = knowledge or Knowledge(settings, self.store, stage=stage)
        self.models = models or Models(settings)
        self.collectors = collectors or Collectors(settings)
        self.stage, self.emit = stage, emit
        self.cache = cache or Cache(settings, stage)
        graph = StateGraph(State)
        for name, fn in (
            ("cache", self.read_cache),
            ("rewrite", self.rewrite),
            ("retrieve", self.retrieve),
            ("assess", self.assess),
            ("acquire", self.online),
            ("recheck", self.recheck),
            ("generate", self.answer),
            ("save", self.save),
        ):
            graph.add_node(name, fn)
        graph.add_edge(START, "cache")
        graph.add_conditional_edges(
            "cache", lambda s: "hit" if s.get("answer") else "miss", {"hit": END, "miss": "rewrite"}
        )
        graph.add_edge("rewrite", "retrieve")
        graph.add_edge("retrieve", "assess")
        graph.add_conditional_edges(
            "assess",
            lambda s: "answer" if s["sufficient"] or s["local_only"] else "online",
            {"answer": "generate", "online": "acquire"},
        )
        graph.add_edge("acquire", "recheck")
        graph.add_edge("recheck", "generate")
        graph.add_edge("generate", "save")
        graph.add_edge("save", END)
        self.graph = graph.compile()

    def ask(self, question: str, history=None, local_only=False) -> Answer:
        if not question.strip():
            raise ValueError("Question cannot be empty.")
        result = self.graph.invoke(
            {
                "question": question.strip(),
                "history": history or [],
                "local_only": local_only,
                "warnings": [],
                "transient": [],
                "online": False,
            }
        )
        return result["answer"]

    def read_cache(self, state):
        self.stage("cache")
        key = cache_key(
            state["question"],
            state["history"],
            self.s,
            self.store.version,
            self.store.corpus_id,
        )
        cached = self.cache.get(key)
        if cached:
            self.emit(cached.text)
            return {"answer": cached}
        return {}

    def rewrite(self, state):
        self.stage("rewrite")
        return {"query": self.models.rewrite(state["question"], state["history"])}

    def retrieve(self, state):
        self.stage("retrieve")
        chunks = self.knowledge.retrieve(state["query"])
        warnings = list(state["warnings"])
        expired = {c.source: c for c in chunks if c.expires_at <= time.time()}
        if expired:
            for source, chunk in expired.items():
                if state["local_only"]:
                    warnings.append(f"Stale source (local-only): {source}")
                    continue
                self.stage("refresh")
                try:
                    old = self.store.source(source)
                    coverage = json.loads(old["coverage"]) if old else {}
                    paths = coverage.get("selected") if coverage.get("targeted") else None
                    limits = (
                        {"max_file_bytes": coverage["max_file_bytes"]}
                        if "max_file_bytes" in coverage
                        else {}
                    )
                    bundle = self.collectors.collect(source, role=chunk.role, paths=paths, **limits)
                    self.knowledge.ingest(bundle)
                except Exception as exc:
                    category = exc.category if isinstance(exc, ServiceError) else "refresh_failed"
                    self.store.event(source, category)
                    warnings.append(f"{category}; using stale source: {source}")
            chunks = self.knowledge.retrieve(state["query"])
        return {"chunks": chunks, "warnings": warnings}

    def assess(self, state):
        self.stage("assess")
        verdict = self.models.assess(state["query"], state["chunks"])
        # No evidence can never be a sufficient answer, including a malformed model verdict.
        return {
            "sufficient": bool(verdict.sufficient and state["chunks"]),
            "queries": verdict.queries[:2] or [state["query"]],
        }

    def online(self, state):
        self.stage("online")
        warnings = list(state["warnings"])
        transient, seen = [], set()
        # Per-round content reads are bounded; GitHub search metadata is not evidence.
        for round_index in range(2):
            query = state["queries"][min(round_index, len(state["queries"]) - 1)]
            if round_index:
                try:
                    local = self.knowledge.retrieve(state["query"])
                except ServiceError:
                    if not transient:
                        raise
                    local = []
                verdict = self.models.assess(state["query"], (local + transient)[:8])
                if verdict.sufficient:
                    break
                query = verdict.queries[0] if verdict.queries else query + " documentation"
            urls = []
            for provider in (self.collectors.github, self.collectors.web):
                try:
                    urls.extend(provider.search(query))
                except ServiceError as exc:
                    warnings.append(str(exc))
                    self.store.event("search", str(exc))
            remaining = 5
            # Prefer targeted missing paths in an already indexed project.
            for chunk in state["chunks"]:
                if not chunk.repo or chunk.source in seen:
                    continue
                seen.add(chunk.source)
                try:
                    paths = self.collectors.github.targeted_paths(chunk.repo, query)
                    if paths:
                        old = self.store.source(chunk.source)
                        selected = json.loads(old["coverage"]).get("selected", [])
                        budget, remaining = remaining, 0
                        bundle = self.collectors.collect(
                            chunk.source,
                            role=chunk.role,
                            paths=list(dict.fromkeys(paths + selected)),
                            file_limit=budget,
                        )
                        # Small targeted reads are stored as separate supplemental sources,
                        # never replacing the larger repository snapshot.
                        supplement = f"https://github.com/{chunk.repo}?" + urlencode(
                            {"paths": json.dumps(paths, ensure_ascii=False)}
                        )
                        bundle.source = supplement
                        for doc in bundle.documents:
                            doc.source = supplement
                        remaining = budget - len(bundle.documents)
                        self._persist_or_keep(bundle, transient, warnings)
                except ServiceError as exc:
                    warnings.append(str(exc))
                if remaining <= 0:
                    break
            for url in dict.fromkeys(urls):
                if remaining <= 0:
                    break
                if url in seen:
                    continue
                seen.add(url)
                # Count failed reads too: inaccessible sources cannot create an unbounded loop.
                remaining -= 1
                try:
                    bundle = self.collectors.collect(url, file_limit=1)
                    self._persist_or_keep(bundle, transient, warnings)
                except ServiceError as exc:
                    warnings.append(str(exc))
                    self.store.event(url, str(exc))
        return {"warnings": warnings, "transient": transient, "online": True}

    def _persist_or_keep(self, bundle, transient, warnings):
        self.stage("ingest")
        try:
            self.knowledge.ingest(bundle)
        except Exception as exc:
            category = exc.category if isinstance(exc, ServiceError) else "index_publish_failed"
            self.store.event(bundle.source, category)
            warnings.append(f"{category}; evidence not saved: {bundle.source}")
            chunker = Chunker(
                self.knowledge.embeddings.tokenizer,
                self.s.embedding_model,
                self.s.chunk_tokens,
                self.s.chunk_overlap,
            )
            for doc in bundle.documents:
                transient.extend(
                    chunker.split(doc, self.s.repo_ttl if bundle.repo else self.s.web_ttl)
                )

    def recheck(self, state):
        self.stage("retrieve")
        warnings = list(state["warnings"])
        try:
            local = self.knowledge.retrieve(state["query"])
        except ServiceError as exc:
            if not state["transient"]:
                raise
            local = []
            warnings.append(f"{exc}; responding only from explicitly unsaved evidence.")
        # Unsaved evidence is kept visible, but never masquerades as persisted knowledge.
        chunks = (state["transient"][:4] + local)[:8] if state["transient"] else local
        verdict = self.models.assess(state["query"], chunks)
        return {
            "chunks": chunks,
            "sufficient": bool(verdict.sufficient and chunks),
            "warnings": warnings,
        }

    def answer(self, state):
        self.stage("answer")
        chunks = state["chunks"]
        if not chunks:
            text = "现有资料不足，无法给出有来源支持的答案。"
            self.emit(text)
        else:
            text = self.models.answer(state["query"], chunks, state["sufficient"], self.emit)
        referenced = {int(x) for x in re.findall(r"\[(\d+)\]", text)}
        valid = {i for i in referenced if 1 <= i <= len(chunks)}
        warnings = list(state["warnings"])
        sufficient = state["sufficient"]
        if chunks and (not valid or referenced != valid):
            warnings.append("Citation validation failed; answer is not cacheable.")
            sufficient = False
        citations = [dict(c.to_dict(), number=i) for i, c in enumerate(chunks, 1) if i in valid]
        transient_ids = {c.id for c in state["transient"]}
        persisted = not any(c["id"] in transient_ids for c in citations)
        if not persisted:
            warnings.append("本次引用包含尚未保存的资料；后续查询不保证可复用。")
        return {
            "answer": Answer(
                text,
                citations,
                "online" if state["online"] else "local",
                sufficient,
                persisted,
                list(dict.fromkeys(warnings)),
                contexts=[dict(c.to_dict(), number=i) for i, c in enumerate(chunks, 1)],
                retrieval=list(self.knowledge.last_trace),
            )
        }

    def save(self, state):
        self.stage("cache_write")
        key = cache_key(
            state["question"],
            state["history"],
            self.s,
            self.store.version,
            self.store.corpus_id,
        )
        self.cache.put(key, state["answer"])
        return {}
