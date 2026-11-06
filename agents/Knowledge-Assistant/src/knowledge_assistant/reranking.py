import math

from .models import Chunk, ServiceError


class Reranker:
    """Local cross-encoder; output scores correspond to input order, not sorted order."""

    def __init__(self, settings):
        self.s = settings
        self._model = None
        self.windowing = {}

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
        if not chunks:
            return []
        try:
            texts = [f"{c.repo or c.source} {c.path}\n{c.text}" for c in chunks]
            model = self.model
            tokenizer = getattr(getattr(model, "model", None), "tokenizer", None)
            self.windowing = {}
            if tokenizer is not None:
                from tokenizers import Tokenizer

                limit = (tokenizer.truncation or {}).get("max_length")
                counter = Tokenizer.from_str(tokenizer.to_str())
                counter.no_truncation()
                counter.no_padding()
                self.windowing = {
                    "pair_token_limit": limit,
                    "windowed_ids": [
                        c.id
                        for c, text in zip(chunks, texts, strict=True)
                        if limit and len(counter.encode(query, text).ids) > limit
                    ],
                }
            scores = list(model.rerank(query, texts, batch_size=4))
        except Exception as exc:
            raise ServiceError("reranker", "model_or_inference_failed") from exc
        try:
            if len(scores) != len(chunks) or not all(math.isfinite(s) for s in scores):
                raise ValueError("Invalid score count or value.")
        except (TypeError, ValueError) as exc:
            raise ServiceError("reranker", "invalid_scores") from exc
        return [float(s) for s in scores]
