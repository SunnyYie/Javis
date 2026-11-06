"""Verify active chunks against preserved originals using the currently locked tokenizer/parser."""

import argparse
import json
from pathlib import Path

from knowledge_assistant.chunking import Chunker
from knowledge_assistant.config import Settings
from knowledge_assistant.models import Bundle, Document
from knowledge_assistant.retrieval import Embeddings, Knowledge
from knowledge_assistant.storage import Store


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repair", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    settings = Settings()
    store = Store(settings.data_dir)
    embeddings = Embeddings(settings)
    chunker = Chunker(
        embeddings.tokenizer,
        settings.embedding_model,
        settings.chunk_tokens,
        settings.chunk_overlap,
    )
    active = {}
    for chunk in store.active_chunks():
        active.setdefault(chunk.source, set()).add(chunk.id)
    mismatches, reports = 0, []
    for row in store.sources():
        coverage = json.loads(row["coverage"])
        snapshot = store.root / "snapshots" / (coverage["snapshot"] + ".json")
        docs = [Document(**d) for d in json.loads(snapshot.read_text())]
        ttl = settings.repo_ttl if row["repo"] else settings.web_ttl
        expected = {c.id for doc in docs for c in chunker.split(doc, ttl)}
        mismatch = expected != active.get(row["source"], set())
        result = {"source": row["source"], "chunks": len(expected), "matches": not mismatch}
        if mismatch:
            mismatches += 1
            if args.repair:
                bundle = Bundle(row["source"], docs, coverage, row["repo"], row["commit_id"])
                result["repair"] = Knowledge(settings, store, embeddings).ingest(bundle)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        reports.append(result)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(reports, indent=2, ensure_ascii=False))
    return bool(mismatches and not args.repair)


if __name__ == "__main__":
    raise SystemExit(main())
