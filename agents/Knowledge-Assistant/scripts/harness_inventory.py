"""Audit existing evidence and discover GitHub sources without executing source content."""

import argparse
import json
from pathlib import Path

from knowledge_assistant.collectors import Collectors
from knowledge_assistant.config import Settings
from knowledge_assistant.models import ServiceError
from knowledge_assistant.storage import Store

TOPICS = {
    "system_context": ["system prompt", "context engineering"],
    "memory_recall": ["memory retrieval", "memory search", "memory recall"],
    "retrieval": ["retrieval", "retriever"],
    "context_compression": ["context compression", "compaction", "summarization"],
    "prompt_cache": ["prompt caching", "cache_control", "cached tokens"],
    "tool_schema": ["tool schema", "inputschema", "function schema"],
    "mcp": ["model context protocol", "mcp server"],
    "skills": ["skill.md", "agent skills"],
    "a2a": ["agent2agent", "a2a protocol"],
    "acp": ["agent client protocol", "agent communication protocol"],
    "permissions": ["permission", "authorization", "allowlist"],
    "sandbox": ["sandbox", "sandboxed"],
    "hitl": ["human-in-the-loop", "human in the loop", "human approval"],
    "retry": ["retry policy", "retry_policy", "retrystrategy"],
    "cost_guard": ["budget", "cost limit", "max_budget"],
    "trace_observability": ["opentelemetry", "observability", "tracing"],
    "replay": ["replay", "time travel", "checkpoint"],
    "graphrag": ["graphrag", "graph rag"],
    "agentic_rag": ["agentic rag", "agentic retrieval"],
}

SEARCHES = [
    ("context", "context engineering agent memory compression"),
    ("protocols", "a2a agent protocol"),
    ("acp", "agent client protocol"),
    ("graphrag", "graphrag"),
    ("agentic_rag", "agentic rag"),
    ("reliability", "agent sandbox observability"),
    ("cost_cache", "llm budget prompt caching"),
]


def audit(store):
    chunks = store.active_chunks()
    topics = {}
    for topic, aliases in TOPICS.items():
        matches = [c for c in chunks if any(a in c.text.lower() for a in aliases)]
        topics[topic] = {
            "keyword_candidates": len(matches),
            "repositories": sorted({c.repo for c in matches if c.repo}),
            "examples": [
                {"url": c.url, "path": c.path, "role": c.role, "excerpt": c.text[:500]}
                for c in sorted(matches, key=lambda c: (c.role != "project", c.path, c.id))[:5]
            ],
            "interpretation": "Discovery only; keyword presence is not verified topic coverage.",
        }
    return {
        "version": store.version,
        "sources": len(store.sources()),
        "chunks": len(chunks),
        "topics": topics,
    }


def discover(collectors):
    rows = []
    for topic, query in SEARCHES:
        for name, provider, search in (
            ("github", collectors.github, query),
            ("web_github", collectors.web, "site:github.com " + query),
        ):
            try:
                row = {
                    "topic": topic,
                    "provider": name,
                    "query": search,
                    "urls": provider.search(search),
                }
            except ServiceError as exc:
                row = {"topic": topic, "provider": name, "query": search, "error": str(exc)}
            rows.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--search", action="store_true")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Use a new output directory.")
    args.output.mkdir(parents=True)
    settings = Settings()
    store = Store(settings.data_dir)
    (args.output / "inventory.json").write_text(
        json.dumps(audit(store), ensure_ascii=False, indent=2)
    )
    if args.search:
        rows = discover(Collectors(settings))
        (args.output / "discovery.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
