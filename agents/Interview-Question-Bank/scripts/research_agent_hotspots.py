"""Read-only corpus audit and live public discovery; never execute repository content."""

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path
from urllib.parse import quote

from interview_question_bank.collectors import GitHubCollector
from interview_question_bank.config import ROOT, get_settings
from interview_question_bank.network import SafeHTTP

QUERIES = [
    "repo:ag-ui-protocol/ag-ui",
    "repo:browser-use/browser-use",
    "repo:OpenHands/OpenHands",
    "repo:dbos-inc/dbos-transact-py",
    "repo:microsoft/agent-lightning",
    "repo:google-agentic-commerce/AP2",
    "repo:modelcontextprotocol/modelcontextprotocol",
    "repo:alexzhang13/rlm",
    "repo:huggingface/smolagents",
]


def connect_readonly(path):
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
    db.row_factory = sqlite3.Row
    return db


def audit(db):
    rows = [
        {"id": r["question_id"], **json.loads(r["payload"])}
        for r in db.execute(
            "SELECT c.question_id,r.payload FROM question_catalog c "
            "JOIN question_records r ON r.id=c.question_id"
        )
    ]
    terms = [
        "AG-UI", "A2UI", "WebMCP", "browser-use", "computer use", "OpenHands",
        "SWE-agent", "ACP", "Temporal", "DBOS", "Agent Lightning", "AP2", "UCP",
        "elicitation", "sampling", "RLM", "CodeAct", "smolagents", "A2A", "Harness",
    ]
    return dict(
        total=len(rows), question_ids=[r["id"] for r in rows],
        terms={
            term: dict(
                titles=[r["title"] for r in rows if term.lower() in r["title"].lower()],
                content_count=sum(term.lower() in json.dumps(r, ensure_ascii=False).lower()
                                  for r in rows),
            )
            for term in terms
        },
    )


def local_search(root):
    # Load the sibling retrieval implementation only for this explicit research operation.
    # Its DB remains read-only; no runtime dependency is added to the interview agent.
    sys.path.insert(0, str(root / "src"))
    from knowledge_assistant.config import Settings
    from knowledge_assistant.retrieval import Knowledge
    from knowledge_assistant.storage import Store

    settings = Settings(llm_model="glm-5.3-flashx")
    store = Store.__new__(Store)
    store.root = settings.data_dir
    store.db = connect_readonly(settings.data_dir / "knowledge.sqlite3")
    knowledge = Knowledge(settings, store)
    results = []
    for query in (
        "agent frontend AG-UI A2UI browser computer use",
        "coding agent harness code execution sandbox subagent",
        "MCP authorization elicitation sampling tasks",
        "agent reinforcement learning Agent Lightning RLM",
        "agent durable execution background scheduling recovery",
    ):
        try:
            chunks = knowledge.retrieve(query)
            results.append(dict(
                query=query, status="ok", trace=knowledge.last_trace,
                hits=[dict(id=c.id, url=c.url, source=c.source, path=c.path,
                           excerpt=c.text[:1200], expires_at=c.expires_at) for c in chunks],
            ))
        except Exception as exc:
            results.append(dict(query=query, status="failed", error=type(exc).__name__,
                                category=getattr(exc, "category", None)))
    store.db.close()
    return results


def research(output, sibling):
    output.mkdir(parents=True, exist_ok=True)
    if (output / "research.json").exists():
        raise ValueError("Research baseline already exists; use a new output directory.")
    settings = get_settings()
    db = connect_readonly(settings.data_dir / "knowledge.sqlite3")
    report = dict(at=time.time(), model=settings.llm_model, baseline=audit(db), discovery=[])
    db.close()

    def save():
        (output / "research.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))

    save()
    http = SafeHTTP()
    github = GitHubCollector(settings, http)
    for query in QUERIES:
        try:
            data = github.api("search/repositories?q=" + quote(query) +
                              "&sort=stars&per_page=3")
            report["discovery"].append(dict(
                kind="github", query=query, status="ok",
                results=[dict(repo=r["full_name"], url=r["html_url"],
                              stars=r["stargazers_count"], updated_at=r["updated_at"],
                              description=r["description"]) for r in data.get("items", [])],
            ))
        except Exception as exc:
            report["discovery"].append(dict(
                kind="github", query=query, status="failed", error=type(exc).__name__,
                category=getattr(exc, "category", None),
            ))
        save()
    for query in (
        "AI agent AG-UI A2UI WebMCP protocol official",
        "AI agent AP2 UCP Agent Lightning recursive language models official",
    ):
        try:
            data = http.json(settings.search_endpoint, payload=dict(
                api_key=settings.tavily_api_key.get_secret_value(),
                query=query, max_results=5,
            ))
            report["discovery"].append(dict(
                kind="tavily", query=query, status="ok",
                results=[{k: r.get(k) for k in ("url", "title", "score")}
                         for r in data.get("results", [])],
            ))
        except Exception as exc:
            report["discovery"].append(dict(
                kind="tavily", query=query, status="failed", error=type(exc).__name__,
                category=getattr(exc, "category", None),
            ))
        save()
    report["knowledge_assistant"] = local_search(sibling)
    save()
    print(json.dumps(dict(
        baseline=report["baseline"]["total"],
        discovery=[dict(kind=r["kind"], query=r["query"], status=r["status"],
                        results=r.get("results"), category=r.get("category"))
                   for r in report["discovery"]],
        local=[dict(query=r["query"], status=r["status"], hits=len(r.get("hits", [])))
               for r in report["knowledge_assistant"]],
    ), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--knowledge-assistant", type=Path,
                        default=ROOT.parent / "Knowledge-Assistant")
    args = parser.parse_args()
    research(args.output, args.knowledge_assistant)
