"""Real CLI/browser imports into the configured library, with per-repository receipts."""

import argparse
import json
import os
import subprocess
import time
from pathlib import Path

from knowledge_assistant.config import ROOT, Settings
from knowledge_assistant.storage import Store


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--files", type=int, default=6)
    parser.add_argument("--tree-pages", type=int, default=3)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--retry-results", type=Path)
    args = parser.parse_args()
    if args.files < 1 or args.tree_pages < 0:
        parser.error("Invalid import limits.")
    args.evidence.mkdir(parents=True, exist_ok=False)
    settings = Settings()
    catalog = json.loads(settings.seeds_file.read_text())
    if args.retry_results:
        failed = {
            r["repo"] for r in json.loads(args.retry_results.read_text()) if r["returncode"] != 0
        }
        catalog = [entry for entry in catalog if entry["repo"] in failed]
    env = dict(
        os.environ,
        GITHUB_IMPORT_MODE="browser",
        MAX_FILES=str(args.files),
        GITHUB_TREE_PAGES=str(args.tree_pages),
    )
    results = []
    for number, entry in enumerate(catalog, 1):
        label = f"{number:02d}-{entry['repo'].replace('/', '--')}"
        single = args.evidence / f"{label}.catalog.json"
        single.write_text(json.dumps([entry]))
        started = time.monotonic()
        try:
            run = subprocess.run(
                [str(ROOT / ".venv/bin/ka"), "seed", "--catalog", str(single.resolve())],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=1800,
            )
            (args.evidence / f"{label}.stdout").write_text(run.stdout)
            (args.evidence / f"{label}.stderr").write_text(run.stderr)
            row = {"repo": entry["repo"], "returncode": run.returncode}
            if run.returncode == 0:
                row.update(json.loads(run.stdout))
                assert 1 <= row["files"] <= args.files and row["chunks"] > 0
            else:
                row["error"] = run.stderr.strip()
        except subprocess.TimeoutExpired:
            row = {"repo": entry["repo"], "returncode": 124, "error": "import_timeout"}
        row["seconds"] = round(time.monotonic() - started, 2)
        results.append(row)
        (args.evidence / "results.json").write_text(
            json.dumps(results, indent=2, ensure_ascii=False)
        )
        print(json.dumps(row, ensure_ascii=False), flush=True)
    store = Store(settings.data_dir)
    sources = [r for r in store.sources() if r["repo"]]
    chunks = store.active_chunks()
    for result in results:
        if result["returncode"] != 0:
            continue
        source = next(r for r in sources if r["source"] == result["source"])
        coverage = json.loads(source["coverage"])
        assert coverage["transport"] == "browser"
        assert len(source["commit_id"]) == 40
        active = [c for c in chunks if c.source == source["source"]]
        assert active and all(
            c.commit == source["commit_id"]
            and f"/blob/{c.commit}/" in c.url
            and "#L" in c.url
            and c.start >= 1
            and c.end >= c.start
            for c in active
        )
    (args.evidence / "source-manifests.json").write_text(
        json.dumps(sources, indent=2, ensure_ascii=False)
    )
    failures = sum(r["returncode"] != 0 for r in results)
    print(f"Passed: {len(results) - failures}; failed: {failures}", flush=True)
    return bool(failures)


if __name__ == "__main__":
    raise SystemExit(main())
