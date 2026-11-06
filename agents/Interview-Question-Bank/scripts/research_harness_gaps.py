"""Audit the current bank and discover public Harness implementation evidence."""

import argparse
import json
import re
import time
from pathlib import Path
from urllib.parse import quote

from expand_agent_hotspots import catalog_digests
from research_agent_hotspots import connect_readonly

from interview_question_bank.collectors import GitHubCollector
from interview_question_bank.config import get_settings
from interview_question_bank.network import SafeHTTP

DIMENSIONS = {
    "Microsoft Agent Framework": r"agent.framework|微软.*框架",
    "CrewAI": r"crewai|crew ai",
    "learn-claude-code": r"learn.claude.code",
    "System context": r"system.prompt|系统提示|上下文装配",
    "Memory compression and recall": r"记忆.*(?:压缩|召回)|(?:压缩|召回).*记忆",
    "Context compression": r"上下文.*压缩|context.*compress|compaction|llmlingua",
    "Prompt cache": r"prompt.cach|prefix.cach|提示缓存|前缀缓存|提示词缓存|cache_control",
    "Tool schema": r"tool.schema|工具.*schema|schema.*工具",
    "MCP and skills": r"\bMCP\b|\bSkills?\b",
    "A2A and ACP": r"\bA2A\b|\bACP\b",
    "Permission tiers": r"权限.*(?:分级|分层|审批)|(?:分级|分层).*权限|permission.mode",
    "Sandbox": r"sandbox|沙箱",
    "HITL": r"HITL|human.in.the.loop|人在回路|人工审批|人工介入",
    "Retry": r"retry|重试",
    "Cost guard": r"cost.guard|预算.*(?:强制|触顶|超限)|成本.*控制",
    "Trace and observability": r"trace|observability|可观测|追踪",
    "Replay": r"replay|回放|重放",
    "GraphRAG": r"graph.?rag|图检索",
    "Agentic RAG": r"agentic.?rag|自主检索|检索.*反思",
}
QUERIES = [
    "repo:microsoft/agent-framework",
    "repo:crewAIInc/crewAI",
    "repo:shareAI-lab/learn-claude-code",
    "graphrag in:name language:Python",
    "agentic rag in:description language:Python",
    "agent context compression in:description",
    "agent sandbox in:description",
    "llm observability in:description",
    "agent permissions in:description",
]
REPOSITORIES = [
    "microsoft/agent-framework", "crewAIInc/crewAI", "shareAI-lab/learn-claude-code",
    "microsoft/graphrag", "HKUDS/LightRAG", "langchain-ai/langgraph",
    "openai/openai-agents-python", "anthropics/claude-agent-sdk-python",
    "microsoft/LLMLingua", "langfuse/langfuse", "e2b-dev/E2B",
    "anthropics/anthropic-sdk-python",
]


def coverage(rows):
    content = [
        json.dumps(
            {k: row[k] for k in ("title", "answer", "key_points", "knowledge_points")
             if k in row}, ensure_ascii=False,
        ) for row in rows
    ]
    return {
        dimension: dict(
            title_count=sum(bool(re.search(pattern, row["title"], re.I)) for row in rows),
            content_count=sum(bool(re.search(pattern, text, re.I)) for text in content),
            examples=[
                dict(id=row["id"], title=row["title"])
                for row in rows if re.search(pattern, row["title"], re.I)
            ][:30],
        ) for dimension, pattern in DIMENSIONS.items()
    }


def repository_details(github, repo, max_file_bytes):
    info = github.api("repos/" + repo)
    revision = github.api(f"repos/{repo}/commits/{info['default_branch']}")["sha"]
    tree = github.api(f"repos/{repo}/git/trees/{revision}?recursive=1")
    paths = [
        row["path"] for row in tree.get("tree", [])
        if row["type"] == "blob" and row.get("mode") != "120000"
        and row.get("size", 0) <= max_file_bytes
        and re.search(r"\.(?:md|mdx|rst|py)$", row["path"])
        and not any(p in {".github", "node_modules", "vendor"}
                    for p in row["path"].split("/"))
    ]
    return dict(
        repo=repo, status="ok", commit=revision, paths=paths,
        truncated=tree.get("truncated", False),
        stars=info["stargazers_count"], pushed_at=info.get("pushed_at"),
        license=(info.get("license") or {}).get("spdx_id") or "unknown",
    )


def research(output, extend=(), queries=()):
    output.mkdir(parents=True, exist_ok=True)
    path = output / "research.json"
    resume = bool(extend or queries)
    if path.exists() and not resume:
        raise ValueError("Use a new output directory; preserve the baseline.")
    settings = get_settings()
    if resume:
        report = json.loads(path.read_text())
        rows = []
    else:
        db = connect_readonly(settings.data_dir / "knowledge.sqlite3")
        rows = [
            dict(id=row[0], **json.loads(row[1]))
            for row in db.execute(
                "SELECT r.id,r.payload FROM question_records r "
                "JOIN question_catalog c ON c.question_id=r.id"
            )
        ]
        report = dict(
            at=time.time(), model=settings.llm_model,
            baseline=dict(total=len(rows), question_ids=[r["id"] for r in rows],
                          record_digests=catalog_digests(db)),
            coverage=coverage(rows), discovery=[], repositories=[],
            limits="Keyword counts are discovery signals, not proof of semantic coverage.",
        )
        db.close()

    def save():
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2))

    save()
    github = GitHubCollector(settings, SafeHTTP())
    for query in (queries if resume else QUERIES):
        try:
            result = github.api("search/repositories?q=" + quote(query) +
                                "&sort=stars&per_page=5")
            entry = dict(query=query, status="ok", results=[
                dict(
                    **{k: row.get(k) for k in
                       ("full_name", "html_url", "stargazers_count", "pushed_at")},
                    description=(row.get("description") or "")[:800],
                )
                for row in result.get("items", [])
            ])
        except Exception as exc:
            entry = dict(query=query, status="failed", error=type(exc).__name__,
                         category=getattr(exc, "category", None))
        report["discovery"].append(entry)
        save()
        print(json.dumps(entry, ensure_ascii=False), flush=True)
        time.sleep(2)
    for repo in (extend if resume else REPOSITORIES):
        try:
            entry = repository_details(github, repo, settings.max_file_bytes)
        except Exception as exc:
            entry = dict(repo=repo, status="failed", error=type(exc).__name__,
                         category=getattr(exc, "category", None))
        report["repositories"].append(entry)
        save()
        print(json.dumps({k: v for k, v in entry.items() if k != "paths"},
                         ensure_ascii=False), flush=True)
    print(json.dumps(dict(
        baseline=report["baseline"]["total"],
        coverage={k: v["title_count"] for k, v in report["coverage"].items()},
    ), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--extend-repo", action="append", default=[])
    parser.add_argument("--query", action="append", default=[])
    args = parser.parse_args()
    research(args.output, args.extend_repo, args.query)
