import json
import logging
import math
import re
import time
from collections import OrderedDict, defaultdict
from pathlib import PurePosixPath

from .chunking import Chunker
from .config import Settings
from .models import Bundle, Chunk, ServiceError, digest
from .reranking import Reranker
from .storage import Store


class Embeddings:
    def __init__(self, settings: Settings):
        self.s = settings
        self._model = None
        self._tokenizer = None
        self._dimension = None
        self._query_cache = OrderedDict()
        self._query_model = None
        self.last_cache_hit = False

    @property
    def model(self):
        if self._model is None:
            from fastembed import TextEmbedding

            self._model = TextEmbedding(
                self.s.embedding_model,
                cache_dir=str(self.s.model_cache_dir or self.s.data_dir / "models"),
                threads=4,
            )
        return self._model

    @property
    def tokenizer(self):
        if self._tokenizer is None:
            from tokenizers import Tokenizer

            self.model.model._ensure_tokenizer()
            truncation = self.model.model.tokenizer.truncation
            if truncation and truncation["max_length"] < self.s.chunk_tokens:
                raise ServiceError("embedding", "chunk_limit_exceeds_model_context")
            # The inference tokenizer must keep batch padding and its safety limit.
            self._tokenizer = Tokenizer.from_str(self.model.model.tokenizer.to_str())
            self._tokenizer.no_truncation()
            self._tokenizer.no_padding()
        return self._tokenizer

    def documents(self, texts: list[str]) -> list[list[float]]:
        return [
            v.tolist() for v in self.model.embed(texts, batch_size=self.s.embedding_batch_size)
        ]

    def query(self, text: str) -> list[float]:
        self.last_cache_hit = False
        if len(self.tokenizer.encode(text).ids) > 512:
            raise ServiceError("embedding", "query_token_limit")
        model = self.model
        capacity = self.s.inference_cache_size
        if model is not self._query_model or not capacity:
            self._query_cache.clear()
            self._query_model = model
        key = (self.s.embedding_model, text)
        if capacity and key in self._query_cache:
            self.last_cache_hit = True
            self._query_cache.move_to_end(key)
            return list(self._query_cache[key])
        vector = next(model.query_embed(text)).tolist()
        if capacity:
            self._query_cache[key] = tuple(vector)
            while len(self._query_cache) > capacity:
                self._query_cache.popitem(last=False)
        return vector

    @property
    def dimension(self):
        if self._dimension is None:
            self._dimension = len(self.query("dimension probe"))
        return self._dimension

    @property
    def fingerprint(self):
        return digest([self.s.embedding_model, self.dimension])


class MilvusIndex:
    def __init__(self, settings: Settings, embeddings: Embeddings):
        self.s, self.embeddings = settings, embeddings
        self._client = None
        self._collection = None

    def ensure(self):
        try:
            from pymilvus import MilvusClient

            if self._client is None:
                self._client = MilvusClient(
                    uri=self.s.milvus_uri,
                    token=self.s.milvus_token.get_secret_value(),
                    timeout=15,
                )
            self._client.list_collections(timeout=15)
        except Exception as exc:
            raise ServiceError("milvus", "unavailable") from exc
        try:
            dim = self.embeddings.dimension
        except Exception as exc:
            raise ServiceError("embedding", "model_load_failed") from exc
        try:
            if self._collection is None:
                self._collection = (
                    self.s.index_namespace + "_" + digest([self.s.embedding_model, dim])[:16]
                )
                if not self._client.has_collection(self._collection):
                    self._client.create_collection(
                        collection_name=self._collection,
                        dimension=dim,
                        id_type="string",
                        max_length=64,
                        auto_id=False,
                        metric_type="COSINE",
                        consistency_level="Strong",
                    )
                description = self._client.describe_collection(self._collection)
                vector = next(f for f in description["fields"] if f["name"] == "vector")
                if int(vector["params"]["dim"]) != dim:
                    raise RuntimeError("Embedding dimension mismatch.")
        except Exception as exc:
            self._collection = None
            raise ServiceError("milvus", "schema_mismatch") from exc

    def write(self, chunks: list[Chunk], vectors: list[list[float]]):
        self.ensure()
        try:
            for i in range(0, len(chunks), 128):
                batch = [
                    {"id": c.id, "vector": v}
                    for c, v in zip(chunks[i : i + 128], vectors[i : i + 128], strict=True)
                ]
                if not batch:
                    continue
                self._client.upsert(self._collection, batch, timeout=60)
                ids = [r["id"] for r in batch]
                found = self._client.get(
                    self._collection,
                    ids=ids,
                    output_fields=["id"],
                    consistency_level="Strong",
                    timeout=30,
                )
                if {r["id"] for r in found} != set(ids):
                    raise RuntimeError("Index write verification failed.")
        except Exception as exc:
            raise ServiceError("milvus", "write_or_verification_failed") from exc

    def search(self, vector: list[float], active: list[str], k=20) -> list[str]:
        self.ensure()
        scored = []
        try:
            for i in range(0, len(active), 1000):
                result = self._client.search(
                    self._collection,
                    data=[vector],
                    filter="id in " + json.dumps(active[i : i + 1000]),
                    limit=k,
                    output_fields=["id"],
                    timeout=30,
                    consistency_level="Strong",
                )
                scored.extend((hit["id"], hit["distance"]) for hit in result[0])
        except Exception as exc:
            raise ServiceError("milvus", "query_failed") from exc
        return [cid for cid, _ in sorted(scored, key=lambda x: x[1], reverse=True)[:k]]


def terms(text: str) -> list[str]:
    import jieba

    jieba.setLogLevel(logging.ERROR)
    identifiers = re.findall(r"[A-Za-z_][A-Za-z0-9_.:/-]*", text)
    chinese = re.findall(r"[\u3400-\u9fff]+", text)
    return [t.lower() for t in identifiers] + [
        t for s in chinese for t in jieba.cut(s) if t.strip()
    ]


def fuse(dense: list[str], lexical: list[str], k=8) -> list[str]:
    scores = defaultdict(float)
    for ranking in (dense, lexical):
        for rank, cid in enumerate(ranking, 1):
            scores[cid] += 1 / (60 + rank)
    return sorted(scores, key=lambda cid: (-scores[cid], cid))[:k]


def path_in_query(path: str, query: str) -> bool:
    return bool(
        path
        and re.search(
            r"(?<![A-Za-z0-9_./-])" + re.escape(path) + r"(?![A-Za-z0-9_./-])", query, re.I
        )
    )


def is_readme(path: str) -> bool:
    return bool(
        re.fullmatch(
            r"README(?:[_.-].*)?\.(?:md|markdown|rst|txt)|README",
            PurePosixPath(path).name,
            re.I,
        )
    )


def canonical_readmes(chunks: list[Chunk], query: str) -> list[Chunk]:
    """Avoid translated README duplicates, without changing stored evidence."""
    families = defaultdict(set)
    for chunk in chunks:
        if chunk.repo and is_readme(chunk.path):
            path = PurePosixPath(chunk.path)
            families[(chunk.source, chunk.commit, str(path.parent))].add(chunk.path)
    preferred = {}
    for family, paths in families.items():
        explicit = {p for p in paths if path_in_query(p, query)}
        canonical = {p for p in paths if PurePosixPath(p).name.lower() == "readme.md"}
        preferred[family] = explicit or canonical or paths
    return [
        c
        for c in chunks
        if not c.repo
        or not is_readme(c.path)
        or c.path in preferred[(c.source, c.commit, str(PurePosixPath(c.path).parent))]
    ]


def select_evidence(ranking, dense, lexical, limit, head=1):
    # Protect bounded recall heads; order the selected set by cross-encoder relevance.
    available = {c.id for c in ranking}
    protected = list(dict.fromkeys(lexical[:head] + dense[:head]))
    protected = [cid for cid in protected if cid in available][:limit]
    chosen = set(protected)
    for chunk in ranking:
        if len(chosen) >= limit:
            break
        chosen.add(chunk.id)
    return [c for c in ranking if c.id in chosen], protected


class Knowledge:
    def __init__(
        self,
        settings: Settings,
        store: Store,
        embeddings=None,
        index=None,
        reranker=None,
        stage=lambda _: None,
    ):
        self.s, self.store = settings, store
        self.embeddings = embeddings or Embeddings(settings)
        self.index = index or MilvusIndex(settings, self.embeddings)
        self.reranker = reranker or Reranker(settings)
        self.stage = stage
        self.last_trace = []
        self._lexical_version = None
        self._lexical_ids = []
        self._bm25 = None

    def ingest(self, bundle: Bundle) -> dict:
        if not bundle.complete:
            raise ServiceError("ingest", "incomplete_snapshot")
        self.store.check_embedding(self.embeddings.fingerprint)
        ttl = self.s.repo_ttl if bundle.repo else self.s.web_ttl
        chunker = Chunker(
            self.embeddings.tokenizer,
            self.s.embedding_model,
            self.s.chunk_tokens,
            self.s.chunk_overlap,
        )
        chunks = [c for d in bundle.documents for c in chunker.split(d, ttl)]
        if not chunks:
            raise ServiceError("ingest", "empty_content")
        reusable = self.store.vectors_by_content(bundle.source)
        missing = list(
            dict.fromkeys(c.content_hash for c in chunks if c.content_hash not in reusable)
        )
        texts = {c.content_hash: c.text for c in chunks}
        new = self.embeddings.documents([texts[h] for h in missing]) if missing else []
        reusable.update(dict(zip(missing, new, strict=True)))
        vectors = [reusable[c.content_hash] for c in chunks]
        dim = self.embeddings.dimension
        if any(len(v) != dim or not all(math.isfinite(n) for n in v) for v in vectors):
            raise ServiceError("embedding", "invalid_vectors")
        self.index.write(chunks, vectors)
        self.store.publish(bundle, chunks, vectors, self.embeddings.fingerprint)
        return {"chunks": len(chunks), "embedded": len(missing), "version": self.store.version}

    def retrieve(self, query: str) -> list[Chunk]:
        self.last_trace = []
        self.index.ensure()  # Even an empty corpus must not hide an unavailable Milvus.
        self.store.check_embedding(self.embeddings.fingerprint)
        chunks = self.store.active_chunks()
        if self.s.readme_policy == "canonical":
            chunks = canonical_readmes(chunks, query)
        if not chunks:
            return []
        named = sorted(
            repo
            for repo in {c.repo for c in chunks if c.repo}
            if re.search(
                r"(?<![A-Za-z0-9_./-])" + re.escape(repo) + r"(?![A-Za-z0-9_./-])",
                query,
                re.I,
            )
        )
        if not named:
            return self._retrieve(query, chunks, 20, 8, candidates=self.s.rerank_candidates)
        if len(named) > 8:
            return self._retrieve(
                query,
                [c for c in chunks if c.repo in named],
                20,
                8,
                candidates=self.s.rerank_candidates,
                scope=tuple(named),
            )
        # Share the original recall/evidence budgets across explicitly named projects.
        groups = []
        for position, repo in enumerate(named):
            count = len(named)
            recall = 20 // count + (position < 20 % count)
            limit = 8 // count + (position < 8 % count)
            candidates = self.s.rerank_candidates // count + (
                position < self.s.rerank_candidates % count
            )
            scoped = [c for c in chunks if c.repo == repo]
            readme_only = bool(re.search(r"\bREADME\b", query, re.I))
            explicit_paths = {c.path for c in scoped if path_in_query(c.path, query)}
            if explicit_paths:
                scoped = [c for c in scoped if c.path in explicit_paths]
            elif readme_only:
                scoped = [
                    c
                    for c in scoped
                    if is_readme(c.path) and PurePosixPath(c.path).parent == PurePosixPath(".")
                ]
            if not scoped:
                groups.append([])
                continue
            groups.append(
                self._retrieve(
                    query,
                    scoped,
                    recall,
                    limit,
                    candidates=candidates,
                    scope=(repo, readme_only),
                    rank_query=query,
                )
            )
        return [
            group[i]
            for i in range(max(map(len, groups), default=0))
            for group in groups
            if i < len(group)
        ]

    def _retrieve(self, query, chunks, recall, limit, *, candidates, scope="", rank_query=None):
        started = time.monotonic()
        by_id = {c.id: c for c in chunks}
        dense = self.index.search(self.embeddings.query(query), list(by_id), recall)
        from rank_bm25 import BM25Okapi

        # File policy can change the corpus even when scope/version stay unchanged.
        version = (self.store.version, scope, digest(list(by_id)))
        if self._lexical_version != version:
            corpus = [terms(c.text) or ["__empty__"] for c in chunks]
            self._bm25 = BM25Okapi(corpus)
            self._lexical_ids = [c.id for c in chunks]
            self._lexical_version = version
        scores = self._bm25.get_scores(terms(query))
        ranked = sorted(range(len(self._lexical_ids)), key=lambda i: scores[i], reverse=True)
        lexical = [self._lexical_ids[i] for i in ranked[:recall] if scores[i] > 0]
        fused = [by_id[cid] for cid in fuse(dense, lexical, candidates) if cid in by_id]
        ranking, scores = fused, []
        if self.s.rerank_enabled and fused:
            self.stage("rerank")
            scores = self.reranker.scores(rank_query or query, fused)
            ranking = [
                c
                for _, c in sorted(
                    zip(scores, fused, strict=True), key=lambda pair: pair[0], reverse=True
                )
            ]
        selected, protected = select_evidence(
            ranking, dense, lexical, limit, self.s.hybrid_head if self.s.rerank_enabled else 0
        )
        self.last_trace.append(
            {
                "query": query,
                "rank_query": rank_query or query,
                "scope": scope,
                "readme_policy": self.s.readme_policy,
                "corpus_chunks": len(chunks),
                "hybrid_head": self.s.hybrid_head,
                "protected": protected,
                "dense": dense,
                "bm25": lexical,
                "rrf": [c.id for c in fused],
                "reranker": self.s.reranker_model if self.s.rerank_enabled else None,
                "scores": dict(zip([c.id for c in fused], scores, strict=True)) if scores else {},
                "windowing": getattr(self.reranker, "windowing", {}) if scores else {},
                "embedding_cache_hit": getattr(self.embeddings, "last_cache_hit", False),
                "reranker_cache_hit": getattr(self.reranker, "last_cache_hit", False)
                if scores else False,
                "selected": [c.id for c in selected],
                "seconds": round(time.monotonic() - started, 4),
            }
        )
        return selected
