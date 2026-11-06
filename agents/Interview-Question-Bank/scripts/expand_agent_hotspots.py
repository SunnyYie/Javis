"""Append evidence-verified hotspot questions without changing the original curriculum."""

import argparse
import json
import re
import subprocess
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import quote, unquote, urljoin, urlsplit

import numpy as np
from lxml import html
from markdown_it import MarkdownIt

from interview_question_bank.bank import (
    Candidate,
    QuestionBank,
    generate_batch,
    nested_answer,
    title_key,
)
from interview_question_bank.collectors import GitHubCollector, WebCollector
from interview_question_bank.config import ROOT, get_settings
from interview_question_bank.models import (
    Bundle,
    Document,
    InterviewQuestion,
    InterviewRequest,
    digest,
)
from interview_question_bank.network import SafeHTTP
from interview_question_bank.retrieval import Knowledge
from interview_question_bank.storage import Store

DEFAULT_PLAN = ROOT / "config/agent_hotspots_20261106.json"
TRANSIENT_REFERENCE = re.compile(
    r"[（(\[]\s*(?:(?:见|参见|对照|参考|依据|证据)\s*)?"
    r"E\d+(?:\s*[,，、及和/&-]\s*E?\d+)*\s*[）)\]]"
)


def save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def clean_references(question):
    question.answer = TRANSIENT_REFERENCE.sub("", question.answer)
    question.key_points = [TRANSIENT_REFERENCE.sub("", p) for p in question.key_points]
    return question


def summarize_checks(checks):
    native = {name: bool(value) for name, value in checks.items()}
    return dict(
        passed=all(native.values()), passed_count=sum(native.values()),
        failed_count=sum(not value for value in native.values()), checks=native,
    )


def catalog_digests(db):
    return {
        row[0]: digest(list(row))
        for row in db.execute(
            "SELECT r.*,c.* FROM question_records r "
            "JOIN question_catalog c ON c.question_id=r.id ORDER BY r.id"
        )
    }


def selected_paths(entries, include, query, limit=6):
    words = query.lower().split()
    docs = [
        e["path"] for e in entries
        if e["type"] == "blob" and e.get("mode") != "120000"
        and e.get("size", 0) <= 262144 and re.search(include, e["path"])
        and not any(p in {"node_modules", "vendor", ".github"} for p in e["path"].split("/"))
    ]
    docs.sort(key=lambda p: (-sum(w in p.lower() for w in words), len(p.split("/")), p))
    readme = next((e["path"] for e in entries if e["path"].lower() == "readme.md"), None)
    # Keep the official overview plus topical bodies, not just repository home pages.
    return list(dict.fromkeys(([readme] if readme else []) + docs))[:limit]


def repository_paths(entries, spec, max_file_bytes):
    limit = spec.get("max_documents", 6)
    if type(limit) is not int or not 1 <= limit <= 30:
        raise ValueError("Repository document budget must be 1-30.")
    if not spec.get("paths"):
        return selected_paths(entries, spec["include"], spec["query"], limit)
    available = {
        e["path"] for e in entries if e["type"] == "blob"
        and e.get("mode") != "120000" and e.get("size", 0) <= max_file_bytes
    }
    if not set(spec["paths"]) <= available:
        raise ValueError("Requested documents missing or exceed safety limits.")
    readme = next((p for p in available if p.lower() == "readme.md"), None)
    paths = list(dict.fromkeys(([readme] if readme else []) + spec["paths"]))
    if len(paths) > limit:
        raise ValueError("Requested documents exceed the declared budget.")
    return paths


def public_metadata(http, source):
    markup = http.text(source, max_bytes=2 * 1024 * 1024)
    nodes = html.fromstring(markup).xpath(
        '//script[@data-target="react-app.embeddedData"]/text()'
    )
    if not nodes:
        raise ValueError("Public GitHub metadata unavailable")
    payload = json.loads(nodes[0])["payload"]
    return payload.get("codeViewRepoRoute", payload.get("codeViewTreeRoute", {})), markup


def public_bundle(http, settings, spec):
    """Read ordinary public pages at a fixed commit; no API-quota claim or code execution."""
    repo = spec["repo"]
    source = "https://github.com/" + repo
    route, markup = public_metadata(http, source)
    revision = route["refInfo"]["currentOid"]
    if not re.fullmatch("[0-9a-f]{40}", revision):
        raise ValueError("Invalid public commit")
    items = route.get("tree", {}).get("items", [])
    paths = [i["path"] for i in items if i["path"].lower() == "readme.md"]
    if not paths:
        paths = ["README.md"]
    texts = {}
    raw = f"https://raw.githubusercontent.com/{repo}/{revision}/"
    texts[paths[0]] = http.text(raw + quote(paths[0]), max_bytes=settings.max_file_bytes)
    linked = []
    for token in MarkdownIt().parse(texts[paths[0]]):
        for child in token.children or []:
            if child.type != "link_open":
                continue
            parsed = urlsplit(urljoin(raw + paths[0], child.attrGet("href") or ""))
            prefix = f"/{repo}/{revision}/"
            if parsed.netloc == "raw.githubusercontent.com" and parsed.path.startswith(prefix):
                path = unquote(parsed.path[len(prefix):])
                if (path.endswith((".md", ".mdx", ".rst", ".bs"))
                        and not any(p in {".", ".."} for p in path.split("/"))):
                    linked.append(path)
    # Explicit public documentation directories are bounded and selected for this topic.
    directory = spec.get("directory")
    failures = []
    if directory:
        try:
            tree_route, _ = public_metadata(http, f"{source}/tree/{revision}/{directory}")
            linked.extend(i["path"] for i in tree_route.get("tree", {}).get("items", [])
                          if i.get("contentType") == "file"
                          and i["path"].endswith((".md", ".mdx", ".rst", ".bs")))
        except Exception as exc:
            failures.append(dict(path=directory, kind="directory",
                                 category=getattr(exc, "category", type(exc).__name__)))
    for item in items:
        if (item.get("contentType") == "file"
                and re.search(spec["include"], item["path"])
                and item["path"].endswith((".md", ".mdx", ".bs"))):
            linked.append(item["path"])
    words = spec["query"].split()
    linked = sorted(dict.fromkeys(linked), key=lambda p: (
        -sum(w in p.lower() for w in words), len(p.split("/")), p,
    ))
    for path in linked:
        if path in texts or path.rsplit("/", 1)[-1].lower() in {
            "agents.md", "claude.md", "contributing.md", "code_of_conduct.md",
            "governance.md", "maintainers.md", "security.md",
        }:
            continue
        if len(texts) >= 6:
            break
        try:
            texts[path] = http.text(raw + quote(path), max_bytes=settings.max_file_bytes)
        except Exception as exc:
            failures.append(dict(path=path, category=getattr(exc, "category", type(exc).__name__)))
    license_links = html.fromstring(markup).xpath("//a[contains(@href,'#')]/text()")
    license_id = next((v for v in ("MIT", "Apache-2.0", "BSD-3-Clause")
                       if any(v.split("-")[0] + " license" in s for s in license_links)), "unknown")
    docs = [Document(source=source, url=f"{source}/blob/{revision}/{quote(path)}",
                     text=text, path=path, repo=repo, commit=revision,
                     license=license_id, role="project") for path, text in texts.items()]
    return Bundle(source, docs, dict(
        selected=list(texts), partial=True, acquisition="public_pages_after_api_failure",
        skipped_reads=failures,
    ), repo=repo, commit=revision)


def collect(output, plan):
    settings = get_settings()
    store = Store(settings.data_dir)
    knowledge = Knowledge(settings, store)
    http = SafeHTTP()
    github = GitHubCollector(settings, http)
    report_path = output / "imports.json"
    report = json.loads(report_path.read_text()) if report_path.exists() else {
        "started_at": time.time(), "attempts": [],
    }
    if any(r.get("category") == "rate_limited" for r in report["attempts"]):
        report["api_rate_limited"] = True
    successful = {r["source"] for r in report["attempts"] if r["status"] == "stored"}
    for spec in plan["repositories"]:
        source = "https://github.com/" + spec["repo"]
        if source in successful:
            continue
        report["in_progress"] = dict(source=source, stage="github_api")
        save(report_path, report)
        print(json.dumps(report["in_progress"]), flush=True)
        try:
            if report.get("api_rate_limited"):
                raise RuntimeError("API quota already exhausted in this run")
            info = github.api("repos/" + spec["repo"])
            revision = github.api(
                f"repos/{spec['repo']}/commits/{info['default_branch']}"
            )["sha"]
            tree = github.api(f"repos/{spec['repo']}/git/trees/{revision}?recursive=1")
            if tree.get("truncated"):
                raise ValueError("Truncated public repository tree")
            paths = repository_paths(tree.get("tree", []), spec, settings.max_file_bytes)
            if not paths:
                raise ValueError("No relevant public documents")
            license_id = (info.get("license") or {}).get("spdx_id") or "unknown"
            docs = []
            for path in paths:
                report["in_progress"] = dict(source=source, stage="fetch_body", path=path)
                save(report_path, report)
                text = http.text(
                    f"https://raw.githubusercontent.com/{spec['repo']}/{revision}/{quote(path)}",
                    max_bytes=settings.max_file_bytes,
                )
                docs.append(Document(
                    source=source, url=f"{source}/blob/{revision}/{quote(path)}",
                    text=text, path=path, repo=spec["repo"], commit=revision,
                    license=license_id, role="project",
                ))
            report["in_progress"] = dict(source=source, stage="vectorize_publish",
                                         documents=len(docs))
            save(report_path, report)
            print(json.dumps(report["in_progress"]), flush=True)
            result = knowledge.ingest(Bundle(
                source, docs, dict(selected=paths, partial=True, topic_query=spec["query"]),
                repo=spec["repo"], commit=revision,
            ))
            entry = dict(
                source=source, status="stored", repo=spec["repo"], commit=revision,
                acquisition="github_api",
                license=license_id, stars=info["stargazers_count"],
                pushed_at=info.get("pushed_at"), paths=paths, **result,
            )
        except Exception as exc:
            category = ("skipped_known_rate_limit" if isinstance(exc, RuntimeError)
                        and report.get("api_rate_limited") else
                        getattr(exc, "category", type(exc).__name__))
            if category == "rate_limited":
                report["api_rate_limited"] = True
            report["attempts"].append(dict(
                at=time.time(), source=source, status="api_failed", category=category,
            ))
            try:
                bundle = public_bundle(http, settings, spec)
                result = knowledge.ingest(bundle)
                entry = dict(
                    source=source, status="stored", repo=spec["repo"], commit=bundle.commit,
                    acquisition="public_pages_after_api_failure",
                    license=bundle.documents[0].license,
                    paths=[d.path for d in bundle.documents], **result,
                )
            except Exception as fallback:
                entry = dict(source=source, status="failed", error=type(fallback).__name__,
                             category=getattr(fallback, "category", None))
        report["attempts"].append(dict(at=time.time(), **entry))
        report["in_progress"] = None
        save(report_path, report)
        print(json.dumps(entry, ensure_ascii=False), flush=True)
    for url in plan["web_sources"]:
        if url in successful:
            continue
        try:
            if url.endswith(".md"):
                text = http.text(url, max_bytes=settings.max_file_bytes)
                if len(text.strip()) < 100 or text.lstrip().lower().startswith(
                    ("<!doctype", "<html")
                ):
                    raise ValueError("Expected a complete plain Markdown body")
                bundle = Bundle(url, [Document(source=url, url=url, text=text, path="body")],
                                dict(selected=["body"], partial=False))
            else:
                bundle = WebCollector(settings, http).collect(url)
            for document in bundle.documents:
                document.role = "project"
            result = knowledge.ingest(bundle)
            entry = dict(source=url, status="stored", paths=["body"], **result)
        except Exception as exc:
            entry = dict(source=url, status="failed", error=type(exc).__name__,
                         category=getattr(exc, "category", None))
        report["attempts"].append(dict(at=time.time(), **entry))
        save(report_path, report)
        print(json.dumps(entry, ensure_ascii=False), flush=True)
    return report


class ScopedStore:
    def __init__(self, store, sources):
        self.store, self.sources = store, set(sources)

    def __getattr__(self, name):
        return getattr(self.store, name)

    def active_chunks(self):
        return [c for c in self.store.active_chunks()
                if c.source in self.sources and c.expires_at > time.time()]


def include_document_status(chunks, active):
    selected = {(c.source, c.path) for c in chunks}
    sources = {c.source for c in chunks}
    headers = [
        c for c in active
        if (
            (c.source, c.path) in selected and c.start <= 20
            and re.search(r"(?mi)^status:\s*\S+", c.text)
        ) or (
            c.source in sources and c.path.lower() == "readme.md" and c.start <= 100
            and re.search(r"(?i)merged|archived|retired|deprecated|part of.*A2A", c.text)
        )
    ]
    seen = {c.id for c in chunks}
    return chunks + [c for c in headers if c.id not in seen]


def export_added(output, bank, baseline):
    rows = [r for r in bank.rows() if r["id"] not in baseline]
    (output / "questions.jsonl").write_text("".join(
        json.dumps(r, ensure_ascii=False) + "\n" for r in rows
    ))
    parts = ["# AI Agent 热点增补题\n", "仅包含本次新增题；完整题库请使用 iqb bank export。\n"]
    for n, row in enumerate(rows, 1):
        parts.extend([
            f"## {n}. {row['title']}\n",
            f"分类：{row['topic_name']} | 难度：{row['difficulty']} | {row['origin']}\n",
            nested_answer(row["answer"]) + "\n",
            "回答要点：\n" + "\n".join(
                "- " + nested_answer(p).strip().replace("\n", "\n  ")
                for p in row["key_points"]
            ),
            "\n来源：\n" + "\n".join(
                "- " + u for u in dict.fromkeys(c["url"] for c in row["citations"])
            ) + "\n",
        ])
    (output / "questions.md").write_text("\n".join(parts))
    return rows


def build(output, plan, workers=4, per_facet=4, retries=2):
    settings = get_settings()
    if settings.llm_model != "glm-5.3-flashx":
        raise ValueError("This expansion requires the authorized glm-5.3-flashx model.")
    baseline = set(json.loads((output / "research.json").read_text())["baseline"]["question_ids"])
    bank = QuestionBank(settings)
    report_path = output / "build.json"
    report = json.loads(report_path.read_text()) if report_path.exists() else dict(
        started_at=time.time(), plan_version=plan["version"], model=settings.llm_model,
        plan_digest=digest(plan), per_facet=per_facet, batches=[], retrieval=[], errors=[],
    )
    if (report["plan_version"] != plan["version"] or report["per_facet"] != per_facet
            or report["plan_digest"] != digest(plan)):
        raise ValueError("Resume settings differ from the saved expansion.")
    if report.get("manual_review"):
        raise ValueError("Reviewed expansion is closed; use a new output and baseline.")
    # No global target or reclassification: existing questions and quotas remain untouched.
    for topic in plan["topics"]:
        topic["quota"] = float("inf")
    counts = Counter()
    for batch in report["batches"]:
        counts[(batch["topic"], batch["facet"])] += batch["added"]
    retrieval_settings = settings.model_copy(update={"retrieval_candidates": 16})
    shared = Knowledge(retrieval_settings, bank.store, embeddings=bank.embeddings)
    retrievers = {
        t["id"]: Knowledge(
            retrieval_settings,
            ScopedStore(bank.store, ["https://github.com/" + r for r in t["repos"]]
                        + t.get("web_sources", []) + t.get("existing_sources", [])),
            embeddings=bank.embeddings, index=shared.index, reranker=shared.reranker,
        ) for t in plan["topics"]
    }
    pools = {}
    active_evidence = [
        c for c in bank.store.active_chunks() if c.expires_at > time.time()
    ]

    def checkpoint():
        added = export_added(output, bank, baseline)
        report.update(
            updated_at=time.time(), total=bank.stats()["total"], added=len(added),
            topics=dict(Counter(r["topic_name"] for r in added)),
            rejections=dict(bank.rejected),
            unfilled=[dict(topic=t["id"], facet=f,
                           missing=max(0, per_facet - counts[(t["id"], f)]))
                      for t in plan["topics"] for f in t["facets"]
                      if counts[(t["id"], f)] < per_facet],
        )
        save(report_path, report)

    try:
        for attempt in range(retries + 1):
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {}

                def consume(future):
                    topic, facet, chunks = futures.pop(future)
                    try:
                        result = future.result()
                        questions = [clean_references(q) for q in result.pop("questions")]
                        old_ids = {r["id"] for r in bank.rows()}
                        # The canonical generator verifies claims against each question's citations.
                        added = bank.add([
                            Candidate(q, chunks, topic, "hotspot", "llm_verified")
                            for q in questions
                        ], plan)
                        ids = [r["id"] for r in bank.rows() if r["id"] not in old_ids]
                        counts[(topic["id"], facet)] += added
                        report["batches"].append(dict(
                            topic=topic["id"], facet=facet, attempt=attempt, added=added,
                            ids=ids, supported_titles=[q.title for q in questions],
                            evidence_ids=[c.id for c in chunks], **result,
                        ))
                        print(json.dumps(dict(topic=topic["id"], facet=facet,
                                              added=added, total=len(bank.vectors)),
                                         ensure_ascii=False), flush=True)
                    except Exception as exc:
                        report["errors"].append(dict(
                            topic=topic["id"], facet=facet, attempt=attempt,
                            error=type(exc).__name__, category=getattr(exc, "category", None),
                        ))
                        print(json.dumps(report["errors"][-1], ensure_ascii=False), flush=True)
                    checkpoint()

                for topic in plan["topics"]:
                    for facet in topic["facets"]:
                        key = (topic["id"], facet)
                        gap = per_facet - counts[key]
                        if gap <= 0:
                            continue
                        # Backpressure keeps the pending model queue bounded while retrieval runs.
                        while len(futures) >= workers:
                            consume(next(as_completed(futures)))
                        if key not in pools:
                            retriever = retrievers[topic["id"]]
                            retrieved = retriever.retrieve(facet, k=12)
                            pools[key] = include_document_status(retrieved, active_evidence)
                            report["retrieval"].append(dict(
                                topic=topic["id"], facet=facet, trace=retriever.last_trace,
                                provenance_ids=[c.id for c in pools[key]
                                                if c.id not in {r.id for r in retrieved}],
                            ))
                            checkpoint()
                        chunks = pools[key]
                        if not chunks:
                            continue
                        existing = [
                            r["title"] for r in bank.rows()
                            if r["topic"] == topic["id"] or any(
                                word.lower() in r["title"].lower() for word in topic["keywords"]
                            )
                        ]
                        request = InterviewRequest(
                            topic=topic["name"],
                            query=facet + "。标题明确点出具体协议或项目，重点考察机制与工程边界。"
                            "资料中的草案、实验性和版本限制必须如实标明，不能宣称已稳定支持。"
                            + topic.get("guidance", ""),
                            knowledge_points=[facet], count=gap, excluded_titles=existing,
                        )
                        future = executor.submit(generate_batch, settings, request, chunks)
                        futures[future] = (topic, facet, chunks)
                for future in as_completed(list(futures)):
                    consume(future)
    finally:
        checkpoint()
    return report


def finalize(output):
    bank = QuestionBank(get_settings())
    baseline = set(json.loads((output / "research.json").read_text())["baseline"]["question_ids"])
    changed = []
    # Formatting only: retain the model-verified claims and all durable evidence IDs.
    with bank.store.db:
        for row in bank.store.db.execute(
            "SELECT r.id,r.payload FROM question_records r "
            "JOIN question_catalog c ON c.question_id=r.id"
        ).fetchall():
            if row["id"] in baseline:
                continue
            question = InterviewQuestion.model_validate_json(row["payload"])
            before = question.model_dump()
            clean_references(question)
            if question.model_dump() != before:
                bank.store.db.execute(
                    "UPDATE question_records SET payload=? WHERE id=?",
                    (question.model_dump_json(), row["id"]),
                )
                changed.append(row["id"])
    export_added(output, bank, baseline)
    history_path = output / "finalization.json"
    previous = json.loads(history_path.read_text()) if history_path.exists() else {}
    ids = list(dict.fromkeys(previous.get("ids", []) + changed))
    runs = previous.get("runs", (
        [dict(at=previous["at"], reformatted=previous["reformatted"])] if previous else []
    ))
    runs.append(dict(at=time.time(), reformatted=len(changed)))
    report = dict(at=time.time(), reformatted=len(ids), this_run=len(changed), ids=ids, runs=runs,
                  preserved="claims, titles, evidence_ids, citations and vectors")
    save(history_path, report)
    print(json.dumps(report, ensure_ascii=False))


def validate(output, plan, baseline_export=None):
    settings = get_settings()
    bank = QuestionBank(settings)
    baseline = set(json.loads((output / "research.json").read_text())["baseline"]["question_ids"])
    rows = bank.rows()
    new = [r for r in rows if r["id"] not in baseline]
    by_id = {r["id"]: r for r in rows}
    build_report = json.loads((output / "build.json").read_text())
    facet_counts = Counter()
    for batch in build_report["batches"]:
        facet_counts[(batch["topic"], batch["facet"])] += batch["added"]
    checks = dict(
        baseline_preserved=baseline <= by_id.keys(),
        all_published_citations_active=all(r["citations_active"] for r in rows),
        bank_exceeds_1000=len(rows) > 1000,
        all_new_citations_active=bool(new) and all(r["citations_active"] for r in new),
        all_new_verified=bool(new) and all(r["review"] == "llm_verified" for r in new),
        kept_batches_match_published_questions=(
            {qid for batch in build_report["batches"] for qid in batch["ids"]}
            == {r["id"] for r in new}
        ),
        all_new_answers_present=all(r["answer"] and r["key_points"] for r in new),
        original_quotes_valid=all(
            r["origin"] != "来源原题" or (
                r["source_question_quote"]
                and any(r["source_question_quote"] in c["text"] for c in r["citations"])
            ) for r in new
        ),
        no_transient_citation_labels=all(
            not TRANSIENT_REFERENCE.search(r["answer"])
            and all(not TRANSIENT_REFERENCE.search(p) for p in r["key_points"]) for r in new
        ),
        unique_normalized_titles=len({title_key(r["title"]) for r in rows}) == len(rows),
        generated_not_raw=not set(by_id) & {c.id for c in bank.store.active_chunks()},
        all_topics_added=all(any(r["topic"] == t["id"] for r in new) for t in plan["topics"]),
        all_new_evidence_fresh=all(c["expires_at"] > time.time()
                                   for r in new for c in r["citations"]),
        all_requested_facets_have_questions=all(
            facet_counts[(t["id"], f)] > 0 for t in plan["topics"] for f in t["facets"]
        ),
        generation_used_hybrid_and_reranker=bool(build_report["retrieval"]) and all(
            r["trace"]["dense_ids"] and "lexical_ids" in r["trace"]
            and r["trace"]["fused_ids"] and r["trace"]["scores"]
            for r in build_report["retrieval"]
        ),
    )
    baseline_digests = json.loads((output / "research.json").read_text())[
        "baseline"
    ].get("record_digests")
    if baseline_digests:
        current = catalog_digests(bank.store.db)
        checks["baseline_records_unchanged"] = (
            set(baseline_digests) == baseline
            and all(current.get(key) == value for key, value in baseline_digests.items())
        )
    if baseline_export:
        previous = [json.loads(line) for line in baseline_export.read_text().splitlines()]
        checks["baseline_records_unchanged"] = (
            len(previous) == len(baseline)
            and {r["id"] for r in previous} == baseline
            and all(by_id.get(r["id"]) == r for r in previous)
        )
    active = {c.id: c for c in bank.store.active_chunks()}
    planned = [s for s in plan["repositories"] if s.get("paths")]
    if planned:
        checks["all_planned_documents_ingested"] = all(
            set(spec["paths"]) <= {
                c.path for c in active.values()
                if c.source == "https://github.com/" + spec["repo"]
            } for spec in planned
        )
    checks["citations_match_current_raw_text"] = all(
        set(r["evidence_ids"]) == {c["id"] for c in r["citations"]}
        and all(c["id"] in active and c["text"] == active[c["id"]].text
                and c["url"] == active[c["id"]].url for c in r["citations"])
        for r in new
    )
    vectors = np.asarray(bank.vectors)
    checks["vectors_valid"] = vectors.shape == (len(rows), 768) and np.isfinite(vectors).all()
    similarity = vectors @ vectors.T
    np.fill_diagonal(similarity, -1)
    checks["semantic_dedup_all_pairs"] = float(similarity.max()) < plan[
        "semantic_duplicate_threshold"
    ]
    bank.index.ensure()
    stored = []
    for n in range(0, len(rows), 200):
        stored.extend(bank.index._client.get(
            bank.index._collection, ids=[r["id"] for r in rows[n:n + 200]],
            output_fields=["id", "vector"], consistency_level="Strong", timeout=30,
        ))
    checks["all_published_vectors_present"] = {r["id"] for r in stored} == set(by_id)
    exclusions = output / "review-exclusions.json"
    if exclusions.exists():
        excluded_ids = [
            r["question"]["id"] for r in json.loads(exclusions.read_text())
        ]
        checks["reviewed_questions_unpublished"] = not set(excluded_ids) & set(by_id)
        checks["reviewed_vectors_removed"] = not bank.index._client.get(
            bank.index._collection, ids=excluded_ids, output_fields=["id"],
            consistency_level="Strong", timeout=30,
        )
    sql = {r[0]: json.loads(r[1]) for r in bank.store.db.execute(
        "SELECT question_id,vector FROM question_catalog"
    )}
    checks["milvus_vectors_match_sql"] = all(
        np.allclose(r["vector"], sql[r["id"]], atol=1e-6) for r in stored
    )
    hits = []
    for topic in plan["topics"]:
        result = bank.search(topic["facets"][0], limit=3, topic=topic["id"])
        checks["search_" + topic["id"]] = len(result) == 3 and all(
            r["id"] not in baseline and r["citations_active"] for r in result
        )
        hits.append(dict(topic=topic["id"], query=topic["facets"][0],
                         results=[dict(id=r["id"], title=r["title"],
                                       urls=[c["url"] for c in r["citations"]]) for r in result]))
    exported = [json.loads(line) for line in (output / "questions.jsonl").read_text().splitlines()]
    checks["incremental_export_exact"] = {r["id"]: r for r in exported} == {
        r["id"]: r for r in new
    }
    checks["markdown_count"] = sum(
        t.type == "heading_open" and t.tag == "h2"
        for t in MarkdownIt().parse((output / "questions.md").read_text())
    ) == len(new)
    report = dict(
        **summarize_checks(checks),
        stats=bank.stats(), added=len(new), max_similarity=float(similarity.max()),
        unfilled=build_report["unfilled"],
        source_count=len(bank.store.sources()), raw_chunks=len(active),
        knowledge_version=bank.store.version, question_collection=bank.index._collection,
        model=settings.llm_model, at=time.time(),
        limits="Live source/model/index/retrieval checks; not independent expert factual review.",
    )
    save(output / "validation.json", report)
    save(output / "retrieval-examples.json", hits)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def terminal_check(output, query="给我 3 道 AG-UI 共享状态与事件协议的面试题"):
    started = time.monotonic()
    command = [
        str(ROOT / ".venv/bin/iqb"), "chat",
        "-q", query,
        "--local-only", "--json",
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=900, cwd=ROOT)
    except (subprocess.TimeoutExpired, OSError) as exc:
        report = dict(at=time.time(), command=command, passed=False,
                      error=type(exc).__name__, seconds=time.monotonic() - started)
        save(output / "terminal.json", report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return report
    try:
        answer = json.loads(result.stdout) if result.returncode == 0 else {}
    except ValueError:
        answer = {}
    checks = dict(
        terminal_exit_zero=result.returncode == 0,
        exactly_three_questions=len(answer.get("questions", [])) == 3,
        sufficient=answer.get("sufficient") is True,
        persisted=answer.get("persisted") is True,
        local_route=answer.get("route") == "local",
        sourced=bool(answer.get("citations")),
        session=bool(answer.get("session")),
    )
    if answer:
        save(output / "chat-example.json", answer)
    report = dict(
        **summarize_checks(checks),
        at=time.time(), command=command, returncode=result.returncode,
        seconds=time.monotonic() - started,
        route=answer.get("route"), warnings=answer.get("warnings", []),
        limits="Real CLI and configured GLM with local RAG; external collection disabled.",
    )
    save(output / "terminal.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["collect", "build", "finalize", "validate", "terminal"])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--workers", type=int, choices=range(1, 5), default=4)
    parser.add_argument("--per-facet", type=int, choices=range(1, 11), default=4)
    parser.add_argument("--retries", type=int, choices=range(0, 6), default=2)
    parser.add_argument("--baseline-export", type=Path)
    parser.add_argument("--query", default="给我 3 道 AG-UI 共享状态与事件协议的面试题")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    config = json.loads(args.plan.read_text())
    if args.mode == "collect":
        collect(args.output, config)
    elif args.mode == "build":
        build(args.output, config, args.workers, args.per_facet, args.retries)
    elif args.mode == "finalize":
        finalize(args.output)
    elif args.mode == "terminal":
        raise SystemExit(0 if terminal_check(args.output, args.query)["passed"] else 1)
    else:
        raise SystemExit(0 if validate(args.output, config, args.baseline_export)["passed"] else 1)
