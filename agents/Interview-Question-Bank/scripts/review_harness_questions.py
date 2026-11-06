"""Unpublish explicitly reviewed new questions, preserving original model verdicts."""

import argparse
import json
import time
from collections import Counter
from pathlib import Path

from expand_agent_hotspots import export_added, save

from interview_question_bank.bank import QuestionBank
from interview_question_bank.config import get_settings


def review(output, decisions, plan):
    baseline = set(json.loads((output / "research.json").read_text())["baseline"]["question_ids"])
    bank = QuestionBank(get_settings())
    rows = {row["id"]: row for row in bank.rows()}
    report = json.loads((output / "build.json").read_text())
    published = {qid for batch in report["batches"] for qid in batch["ids"]}
    if not decisions or len({d["id"] for d in decisions}) != len(decisions):
        raise ValueError("Review decisions must be unique and nonempty.")
    for decision in decisions:
        row = bank.store.db.execute(
            "SELECT plan_version FROM question_catalog WHERE question_id=?", (decision["id"],),
        ).fetchone()
        if (decision["id"] in baseline or decision["id"] not in rows
                or decision["id"] not in published
                or not decision.get("reason") or row["plan_version"] != plan["version"]):
            raise ValueError("Review can only unpublish this expansion's new questions.")
    rejected = [
        dict(question=rows[d["id"]], reason=d["reason"], reviewed_at=time.time())
        for d in decisions
    ]
    save(output / "review-exclusions.json", rejected)
    ids = {d["id"] for d in decisions}
    # Unpublish first: a leftover vector is not visible to catalog-filtered retrieval.
    with bank.store.db:
        for qid in ids:
            bank.store.db.execute("DELETE FROM question_catalog WHERE question_id=?", (qid,))
            bank.store.db.execute("DELETE FROM question_records WHERE id=?", (qid,))
    bank.index.ensure()
    bank.index._client.delete(bank.index._collection, ids=sorted(ids), timeout=30)
    leftover = bank.index._client.get(
        bank.index._collection, ids=sorted(ids), output_fields=["id"],
        consistency_level="Strong", timeout=30,
    )
    if leftover:
        raise RuntimeError("Review unpublication succeeded; vector cleanup not yet confirmed.")
    facet_counts = Counter()
    for batch in report["batches"]:
        removed = set(batch["ids"]) & ids
        if removed:
            batch.setdefault("model_verified_added_before_review", batch["added"])
            batch.setdefault("model_verified_ids_before_review", list(batch["ids"]))
            batch["review_excluded_ids"] = sorted(removed)
            batch["added"] -= len(removed)
            batch["ids"] = [qid for qid in batch["ids"] if qid not in removed]
        facet_counts[(batch["topic"], batch["facet"])] += batch["added"]
    added = export_added(output, bank, baseline)
    report.update(
        updated_at=time.time(), total=bank.stats()["total"], added=len(added),
        topics=dict(Counter(r["topic_name"] for r in added)),
        manual_review=dict(excluded=sorted(ids), vectors_removed=True),
        unfilled=[
            dict(topic=t["id"], facet=f,
                 missing=report["per_facet"] - facet_counts[(t["id"], f)])
            for t in plan["topics"] for f in t["facets"]
            if facet_counts[(t["id"], f)] < report["per_facet"]
        ],
    )
    save(output / "build.json", report)
    print(json.dumps(dict(unpublished=len(ids), total=report["total"]), ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--decisions", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    args = parser.parse_args()
    review(args.output, json.loads(args.decisions.read_text()), json.loads(args.plan.read_text()))
