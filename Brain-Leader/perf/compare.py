"""Summarize saved measurements without rerunning services or model requests."""
import argparse
import json
from pathlib import Path

from perf.benchmark import write


def compare(root):
    def read(path):
        return json.loads((root / path).read_text())

    checks, rows = {}, []
    before = read("production-before.json")
    for kind in ("knowledge", "questions"):
        baseline = read(f"baseline/{kind}/profile-results.json")
        optimized = read(f"optimized/{kind}/profile-results.json")
        checks[f"{kind}_five_topic_ids_and_order"] = {
            topic: value["ids"] for topic, value in baseline["retrieval"].items()
        } == {
            topic: value["ids"] for topic, value in optimized["retrieval"].items()
        }
        checks[f"{kind}_snapshot_count"] = (
            baseline["snapshots"] == optimized["snapshots"]
        )
        checks[f"{kind}_direct_profile_ids_and_order"] = (
            read(f"baseline/{kind}-direct-profile/results.json")["retrieval_ids"]
            == read(f"optimized/{kind}-direct-profile/results.json")["retrieval_ids"]
        )
        for phase in ("baseline", "optimized"):
            checks[f"{phase}_{kind}_production_identity_version"] = (
                read(f"{phase}/{kind}/production-after.json") == before
            )
            for path in sorted((root / phase / kind).glob("*-u*.json")):
                metrics = json.loads(path.read_text())
                rows.append({
                    "phase": phase, "kind": kind, "scenario": path.stem,
                    "evidence": str(path.relative_to(root)), **metrics,
                })
    return {
        "checks": checks, "all_checks_passed": all(checks.values()),
        "request_failures": sum(row["failures"] for row in rows),
        "rows": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    result = compare(args.root)
    write(args.root / "comparison.json", result)
    print("phase kind scenario samples QPS P99_ms failures embedding/reranker_hits")
    for row in result["rows"]:
        tail = f'{row["p99_ms"]:.2f}' if row["p99_ms"] is not None else "unknown"
        print(
            f'{row["phase"]} {row["kind"]} {row["scenario"]} {row["successes"]} '
            f'{row["successful_qps"]:.4f} {tail} {row["failures"]} '
            f'{row.get("embedding_cache_hits", 0)}/{row.get("reranker_cache_hits", 0)}'
        )
    print(json.dumps(result["checks"], ensure_ascii=False))
    if not result["all_checks_passed"] or result["request_failures"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
