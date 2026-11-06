"""Import explicitly selected GitHub files via the existing safe browser collector."""

import argparse
import json
import time
from pathlib import Path

from knowledge_assistant.collectors import Collectors, repository
from knowledge_assistant.config import ROOT, Settings
from knowledge_assistant.models import ServiceError
from knowledge_assistant.retrieval import Knowledge
from knowledge_assistant.storage import Store


def import_entry(entry, collectors, knowledge, store):
    repo = repository(entry["repo"])
    if (
        not repo
        or not entry["paths"]
        or entry["role"] not in {"project", "tutorial", "third_party"}
    ):
        raise ValueError("Invalid source entry.")
    source = f"https://github.com/{repo}"
    paths = list(dict.fromkeys(entry["paths"]))
    if old := store.source(source):
        # A targeted addition must not remove files already published for this source.
        paths = list(dict.fromkeys(paths + json.loads(old["coverage"]).get("selected", [])))
    if len(paths) > collectors.github.s.max_files:
        raise ValueError("Selected and existing files exceed the configured file budget.")
    bundle = collectors.collect(source, role=entry["role"], paths=paths)
    if set(paths) != {doc.path for doc in bundle.documents}:
        raise ServiceError("harness_import", "incomplete_selected_files")
    print(
        json.dumps({"phase": "embed_publish", "repo": repo, "files": len(bundle.documents)}),
        flush=True,
    )
    result = knowledge.ingest(bundle)
    return {
        "repo": bundle.repo,
        "source": bundle.source,
        "commit": bundle.commit,
        "files": len(bundle.documents),
        "bytes": sum(len(d.text.encode()) for d in bundle.documents),
        "topics": entry.get("topics", []),
        "paths": [d.path for d in bundle.documents],
        **result,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=ROOT / "harness-sources.json")
    parser.add_argument("--repo", action="append")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    entries = json.loads(args.catalog.read_text())
    if args.repo:
        entries = [e for e in entries if e["repo"] in args.repo]
    if not entries:
        parser.error("No selected repositories.")
    settings = Settings().model_copy(update={"github_import_mode": "browser"})
    store = Store(settings.data_dir)
    collectors = Collectors(settings, progress=lambda page: print(page, flush=True))
    knowledge = Knowledge(settings, store)
    before = {
        "version": store.version,
        "sources": len(store.sources()),
        "chunks": len(store.active_chunks()),
    }
    rows = []
    for entry in entries:
        started = time.monotonic()
        try:
            row = {"ok": True, **import_entry(entry, collectors, knowledge, store)}
        except Exception as exc:
            row = {
                "ok": False,
                "repo": entry["repo"],
                "error": str(exc) if isinstance(exc, ServiceError) else type(exc).__name__,
            }
        row["seconds"] = round(time.monotonic() - started, 2)
        rows.append(row)
        report = {
            "before": before,
            "after": {
                "version": store.version,
                "sources": len(store.sources()),
                "chunks": len(store.active_chunks()),
            },
            "results": rows,
        }
        (args.output / "results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps(row, ensure_ascii=False), flush=True)
    return int(any(not r["ok"] for r in rows))


if __name__ == "__main__":
    raise SystemExit(main())
