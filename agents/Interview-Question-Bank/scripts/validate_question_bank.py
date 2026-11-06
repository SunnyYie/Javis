"""Read the real production bank and validate published IDs, evidence, quotas and retrieval."""

import argparse
import json
import subprocess
import time
from pathlib import Path

import numpy as np
from markdown_it import MarkdownIt

from interview_question_bank.bank import QuestionBank, load_plan, title_key
from interview_question_bank.config import ROOT, get_settings


def validate(output):
    settings = get_settings()
    bank = QuestionBank(settings)
    plan = load_plan()
    rows = bank.rows()
    checks = {}

    def check(name, condition):
        checks[name] = bool(condition)

    by_id = {r["id"]: r for r in rows}
    check("question_count_1000", len(rows) == 1000)
    check("all_topic_quotas", all(
        bank.counts[t["id"]] == t["quota"] for t in plan["topics"]
    ))
    check("hot_count_765", sum(r["hot"] for r in rows) == 765)
    check("harness_count_100", bank.counts["harness"] == 100)
    check("normalized_titles_unique", len({title_key(r["title"]) for r in rows}) == len(rows))
    check("all_citations_active", all(r["citations_active"] for r in rows))
    check("all_answers_present", all(r["answer"].strip() and r["key_points"] for r in rows))
    check("citation_ids_match", all(
        set(r["evidence_ids"]) == {c["id"] for c in r["citations"]}
        and all(c["url"].startswith("https://") and c["text"].strip() for c in r["citations"])
        for r in rows
    ))
    check("original_question_quotes", all(
        r["origin"] != "来源原题" or (
            r["source_question_quote"]
            and any(r["source_question_quote"] in c["text"] for c in r["citations"])
        ) for r in rows
    ))
    documents = []
    for source in bank.store.sources():
        snapshot = json.loads(source["coverage"])["snapshot"]
        documents.extend(json.loads(
            (bank.store.root / "snapshots" / f"{snapshot}.json").read_text()
        ))
    text_by_doc = {(d["source"], d["path"]): d["text"] for d in documents}
    check("native_answers_exactly_in_snapshots", all(
        r["review"] != "source_exact" or any(
            r["answer"] in text_by_doc.get((c["source"], c["path"]), "")
            for c in r["citations"]
        ) for r in rows
    ))
    vectors = np.asarray(bank.vectors)
    check("vectors_finite_768_dimensions", vectors.shape == (1000, 768)
          and np.isfinite(vectors).all())
    similarities = vectors @ vectors.T
    np.fill_diagonal(similarities, -1)
    max_similarity = float(similarities.max())
    check("all_pairs_below_duplicate_threshold",
          max_similarity < plan["semantic_duplicate_threshold"])
    bank.index.ensure()
    stored = []
    for start in range(0, len(rows), 200):
        stored.extend(bank.index._client.get(
            bank.index._collection, ids=[r["id"] for r in rows[start:start + 200]],
            output_fields=["id", "vector"], consistency_level="Strong", timeout=30,
        ))
    check("milvus_contains_all_published_ids", {r["id"] for r in stored} == set(by_id))
    sql_vectors = {
        r[0]: np.asarray(json.loads(r[1]))
        for r in bank.store.db.execute("SELECT question_id,vector FROM question_catalog")
    }
    check("milvus_vectors_match_sqlite", all(
        np.allclose(r["vector"], sql_vectors[r["id"]], atol=1e-6) for r in stored
    ))
    raw_ids = {c.id for c in bank.store.active_chunks()}
    check("derived_questions_excluded_from_raw_evidence", not set(by_id) & raw_ids)
    searches = []
    for topic in plan["topics"]:
        query = topic["name"] + " " + topic["facets"][0]
        hits = bank.search(query, limit=3, topic=topic["id"])
        ok = len(hits) == 3 and all(
            h["id"] in by_id and h["topic"] == topic["id"] and h["citations_active"]
            for h in hits
        )
        check("real_search_" + topic["id"], ok)
        searches.append(dict(
            topic=topic["id"], query=query, passed=ok,
            hits=[dict(id=h["id"], title=h["title"],
                       urls=list(dict.fromkeys(c["url"] for c in h["citations"])))
                  for h in hits],
        ))
    executable = ROOT / ".venv/bin/iqb"
    cli = subprocess.run(
        [str(executable), "bank", "search", "-q", "Harness 执行循环与失败恢复",
         "--topic", "harness", "--limit", "3"],
        capture_output=True, text=True, timeout=180, cwd=ROOT,
    )
    try:
        cli_rows = json.loads(cli.stdout) if cli.returncode == 0 else []
    except ValueError:
        cli_rows = []
    check("terminal_search_new_process", cli.returncode == 0 and len(cli_rows) == 3
          and all(r["id"] in by_id and r["topic"] == "harness" for r in cli_rows))
    exported = [json.loads(line) for line in (output / "questions.jsonl").read_text().splitlines()]
    check("jsonl_export_matches_database", {r["id"]: r for r in exported} == by_id
          and len(exported) == len(rows))
    markdown = (output / "questions.md").read_text()
    check("markdown_export_question_count",
          sum(t.type == "heading_open" and t.tag == "h2"
              for t in MarkdownIt().parse(markdown)) == len(rows))
    report = dict(
        passed=all(checks.values()), checks=checks,
        passed_count=sum(checks.values()), failed_count=sum(not v for v in checks.values()),
        stats=bank.stats(), model=settings.llm_model, source_version=bank.store.version,
        source_count=len(bank.store.sources()), raw_chunks=len(raw_ids),
        question_collection=bank.index._collection, max_pairwise_similarity=max_similarity,
        terminal_command=cli.args, terminal_returncode=cli.returncode,
        terminal_ids=[r["id"] for r in cli_rows], at=time.time(),
        limits="Structural/source-copy checks and live retrieval; not a factual review of all answers.",
    )
    (output / "retrieval-examples.json").write_text(
        json.dumps(searches, ensure_ascii=False, indent=2)
    )
    (output / "validation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report["passed"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    raise SystemExit(0 if validate(arguments.output) else 1)
