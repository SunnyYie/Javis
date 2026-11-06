import math
from collections import OrderedDict

from .models import Chunk, ServiceError


class Reranker:
    """Local cross-encoder; output scores correspond to input order, not sorted order."""

    def __init__(self, settings):
        self.s = settings
        self._model = None
        self.windowing = {}
        self._score_cache = OrderedDict()
        self._score_model = None
        self._counter_source = None
        self._counter = None
        self.last_cache_hit = False

    @property
    def model(self):
        if self._model is None:
            from fastembed.rerank.cross_encoder import TextCrossEncoder

            self._model = TextCrossEncoder(
                self.s.reranker_model,
                cache_dir=str(self.s.model_cache_dir or self.s.data_dir / "models"),
                threads=4,
                providers=["CPUExecutionProvider"],
            )
        return self._model

    def scores(self, query: str, chunks: list[Chunk]) -> list[float]:
        self.last_cache_hit = False
        if not chunks:
            return []
        try:
            texts = [f"{c.repo or c.source} {c.path}\n{c.text}" for c in chunks]
            model = self.model
            capacity = self.s.inference_cache_size
            if model is not self._score_model or not capacity:
                self._score_cache.clear()
                self._score_model = model
            tokenizer = getattr(getattr(model, "model", None), "tokenizer", None)
            self.windowing = {}
            if tokenizer is not None:
                from tokenizers import Tokenizer

                limit = (tokenizer.truncation or {}).get("max_length")
                if tokenizer is not self._counter_source:
                    self._counter = Tokenizer.from_str(tokenizer.to_str())
                    self._counter.no_truncation()
                    self._counter.no_padding()
                    self._counter_source = tokenizer
                counter = self._counter
                self.windowing = {
                    "pair_token_limit": limit,
                    "windowed_ids": [
                        c.id
                        for c, text in zip(chunks, texts, strict=True)
                        if limit and len(counter.encode(query, text).ids) > limit
                    ],
                }
            rank = model.rerank
            key = (
                self.s.reranker_model, self.s.reranker_batch_size, query, tuple(texts),
                getattr(rank, "__func__", rank),
            )
            if capacity and key in self._score_cache:
                self.last_cache_hit = True
                self._score_cache.move_to_end(key)
                return list(self._score_cache[key])
            scores = list(rank(query, texts, batch_size=self.s.reranker_batch_size))
        except Exception as exc:
            raise ServiceError("reranker", "model_or_inference_failed") from exc
        try:
            if len(scores) != len(chunks) or not all(math.isfinite(s) for s in scores):
                raise ValueError("Invalid score count or value.")
        except (TypeError, ValueError) as exc:
            raise ServiceError("reranker", "invalid_scores") from exc
        values = [float(s) for s in scores]
        if capacity:
            self._score_cache[key] = tuple(values)
            while len(self._score_cache) > capacity:
                self._score_cache.popitem(last=False)
        return values
