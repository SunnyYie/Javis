"""Real local retrieval checks and opt-in GLM CLI answers for the Harness expansion."""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from knowledge_assistant.config import ROOT, Settings
from knowledge_assistant.retrieval import Knowledge
from knowledge_assistant.storage import Store


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--checks", type=Path, help="Separate focused checks; does not replace the frozen set."
    )
    parser.add_argument("--qa", action="store_true", help="Also call the real remote LLM.")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    cases = json.loads((args.checks or ROOT / "eval/harness_checks.json").read_text())
    settings = Settings()
    if args.qa and settings.model_settings()["model"] != "glm-5.3-flashx":
        parser.error("This acceptance run requires glm-5.3-flashx.")
    store = Store(settings.data_dir)
    knowledge = Knowledge(settings, store)
    rows = []
    for case in cases:
        started = time.monotonic()
        chunks = knowledge.retrieve(case["question"])
        found = [
            c for c in chunks if c.repo.lower() == case["repo"].lower() and c.path in case["paths"]
        ]
        row = {
            "topic": case["topic"],
            "question": case["question"],
            "ok": bool(found),
            "seconds": round(time.monotonic() - started, 2),
            "contexts": [c.to_dict() for c in chunks],
            "retrieval": knowledge.last_trace,
        }
        rows.append(row)
        (args.output / "retrieval.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2))
        print(json.dumps({k: row[k] for k in ("topic", "ok", "seconds")}), flush=True)
    answers = []
    if args.qa:
        env = dict(
            os.environ,
            PYTHONPATH=str(ROOT / "src"),
            PYTHONDONTWRITEBYTECODE="1",
            BROWSER_ENABLED="false",
            TAVILY_API_KEY="",
            GITHUB_TOKEN="",
        )
        qa = (
            cases
            if args.checks
            else [c for c in cases if c["topic"] in {"system_context", "memory_recall", "hitl"}]
        )
        comparisons = [
            {
                "topic": "protocol_comparison",
                "question": "根据 a2aproject/A2A、agentclientprotocol/agent-client-protocol 和 i-am-bee/acp，区分 A2A、Agent Client Protocol、Agent Communication Protocol 的定位与当前项目状态。不要把两个 ACP 混为一谈。",
                "required_repos": [
                    "a2aproject/A2A",
                    "agentclientprotocol/agent-client-protocol",
                    "i-am-bee/acp",
                ],
            },
            {
                "topic": "rag_comparison",
                "question": "根据 microsoft/graphrag 和 langchain-ai/docs，GraphRAG 的 local/global search 与 Agentic RAG 的检索、相关性判断和问题改写有什么不同？",
                "required_repos": ["microsoft/graphrag", "langchain-ai/docs"],
            },
        ]
        if not args.checks:
            qa.extend(comparisons)
        for case in qa:
            started = time.monotonic()
            command = [
                sys.executable,
                "-B",
                "-c",
                "from knowledge_assistant.cli import app; app()",
                "chat",
                "--local-only",
                "--json",
                "-q",
                case["question"],
            ]
            run = subprocess.run(
                command, cwd=ROOT, env=env, capture_output=True, text=True, timeout=600
            )
            (args.output / f"{case['topic']}.stdout").write_text(run.stdout)
            if run.stderr:
                (args.output / f"{case['topic']}.stderr").write_text(run.stderr)
            answer = json.loads(run.stdout) if run.returncode == 0 else {}
            required = case.get("required_repos", [case["repo"]] if "repo" in case else [])
            cited = {c.get("repo", "").lower() for c in answer.get("citations", [])}
            row = {
                "topic": case["topic"],
                "question": case["question"],
                "returncode": run.returncode,
                "seconds": round(time.monotonic() - started, 2),
                "ok": bool(
                    run.returncode == 0
                    and answer.get("sufficient")
                    and answer.get("persisted")
                    and answer.get("citations")
                    and answer.get("route") in {"local", "cache"}
                    and all(repo.lower() in cited for repo in required)
                ),
                "answer": answer,
            }
            answers.append(row)
            print(json.dumps({k: row[k] for k in ("topic", "ok", "seconds")}), flush=True)
        cached = subprocess.run(
            [
                sys.executable,
                "-B",
                "-c",
                "from knowledge_assistant.cli import app; app()",
                "chat",
                "--local-only",
                "--json",
                "-q",
                qa[0]["question"],
            ],
            cwd=ROOT,
            env=dict(
                env,
                MILVUS_URI="http://127.0.0.1:1",
                LLM_API_KEY="",
                LLM_ENV_FILE=str(args.output.resolve() / "nonexistent-provider.env"),
            ),
            capture_output=True,
            text=True,
            timeout=60,
        )
        (args.output / "cache.stdout").write_text(cached.stdout)
        if cached.stderr:
            (args.output / "cache.stderr").write_text(cached.stderr)
        cache_answer = json.loads(cached.stdout) if cached.returncode == 0 else {}
        answers.append(
            {
                "topic": "cache_without_vector_or_llm",
                "returncode": cached.returncode,
                "ok": bool(
                    cached.returncode == 0
                    and cache_answer.get("route") == "cache"
                    and cache_answer.get("text") == answers[0]["answer"].get("text")
                ),
            }
        )
        (args.output / "answers.json").write_text(json.dumps(answers, ensure_ascii=False, indent=2))
    report = {
        "knowledge_version": store.version,
        "model": settings.model_settings()["model"],
        "retrieval_passed": sum(r["ok"] for r in rows),
        "retrieval_total": len(rows),
        "qa_passed": sum(r["ok"] for r in answers),
        "qa_total": len(answers),
        "limitations": "Expected-file retrieval and model sufficiency are not factual correctness proofs.",
    }
    (args.output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False), flush=True)
    return int(any(not r["ok"] for r in rows + answers))


if __name__ == "__main__":
    raise SystemExit(main())
