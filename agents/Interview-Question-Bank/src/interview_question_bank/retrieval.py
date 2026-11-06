import json
import logging
import math
import re
import time
from collections import defaultdict

from .chunking import Chunker
from .config import Settings
from .models import Bundle, Chunk, ServiceError, digest
from .storage import Store


class Embeddings:
    def __init__(self, settings: Settings):
        self.s = settings
        self._model = None
        self._tokenizer = None
        self._dimension = None

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
        return [v.tolist() for v in self.model.embed(texts, batch_size=16)]

    def query(self, text: str) -> list[float]:
        if len(self.tokenizer.encode(text).ids) > 512:
            raise ServiceError("embedding", "query_token_limit")
        return next(self.model.query_embed(text)).tolist()

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


class Reranker:
    def __init__(self, settings: Settings):
        self.s = settings
        self._model = None

    def scores(self, query: str, chunks: list[Chunk]) -> list[float]:
        try:
            if self._model is None:
                from fastembed.rerank.cross_encoder import TextCrossEncoder

                self._model = TextCrossEncoder(
                    self.s.reranker_model,
                    cache_dir=str(self.s.model_cache_dir or self.s.data_dir / "models"),
                    threads=4,
                )
            scores = list(self._model.rerank(
                query, [c.text for c in chunks], batch_size=self.s.reranker_batch_size,
            ))
            if len(scores) != len(chunks) or not all(math.isfinite(v) for v in scores):
                raise ValueError("Invalid cross-encoder scores")
            return [float(v) for v in scores]
        except Exception as exc:
            raise ServiceError("reranker", "model_or_scoring_failed") from exc


class Knowledge:
    def __init__(self, settings: Settings, store: Store, embeddings=None, index=None, reranker=None):
        self.s, self.store = settings, store
        self.store.check_index(settings.index_namespace)
        self.embeddings = embeddings or Embeddings(settings)
        self.index = index or MilvusIndex(settings, self.embeddings)
        self.reranker = reranker or Reranker(settings)
        self.last_trace = {}
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

    def retrieve(self, query: str, k=8, *, rerank_query: str | None = None) -> list[Chunk]:
        if not 1 <= k <= 200:
            raise ValueError("Retrieval k must be between 1 and 200.")
        started = time.monotonic()
        self.last_trace = dict(
            self.s.retrieval_settings(), query=query, rerank_query=rerank_query or query,
            k=k, dense_ids=[], lexical_ids=[], fused_ids=[], scores=[], final_ids=[],
        )
        self.index.ensure()  # Even an empty corpus must not hide an unavailable Milvus.
        self.store.check_embedding(self.embeddings.fingerprint)
        chunks = self.store.active_chunks()
        if not chunks:
            return []
        by_id = {c.id: c for c in chunks}
        recall = max(k, self.s.retrieval_candidates)
        dense = self.index.search(self.embeddings.query(query), list(by_id), recall)
        from rank_bm25 import BM25Okapi

        version = self.store.version
        if self._lexical_version != version:
            corpus = [terms(c.text) or ["__empty__"] for c in chunks]
            self._bm25 = BM25Okapi(corpus)
            self._lexical_ids = [c.id for c in chunks]
            self._lexical_version = version
        scores = self._bm25.get_scores(terms(query))
        ranked = sorted(range(len(self._lexical_ids)), key=lambda i: scores[i], reverse=True)
        lexical = [self._lexical_ids[i] for i in ranked[:recall] if scores[i] > 0]
        fused = [cid for cid in fuse(dense, lexical, recall) if cid in by_id]
        candidates = [by_id[cid] for cid in fused]
        rerank_started = time.monotonic()
        scored = []
        if self.s.reranker_enabled and candidates:
            values = self.reranker.scores(rerank_query or query, candidates)
            if len(values) != len(candidates) or not all(math.isfinite(v) for v in values):
                raise ServiceError("reranker", "invalid_scores")
            scored = [{"id": c.id, "score": float(v)}
                      for c, v in zip(candidates, values, strict=True)]
            candidates = [
                c for c, _ in sorted(zip(candidates, values, strict=True),
                                     key=lambda pair: -pair[1])
            ]
        result = candidates[:k]
        self.last_trace.update(
            dense_ids=dense, lexical_ids=lexical, fused_ids=fused, scores=scored,
            final_ids=[c.id for c in result],
            rerank_seconds=round(time.monotonic() - rerank_started, 4),
            seconds=round(time.monotonic() - started, 4),
        )
        return result
