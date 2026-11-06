"""Opt-in real CLI/model trace smoke using cloned corpora and synthetic resume."""

import argparse
import json
import os
import sqlite3
import subprocess
from pathlib import Path

from javis_tools.adapters import Assistants
from javis_tools.isolation import clone_corpus
from javis_tools.tracing import summarize

from brain_leader.config import ROOT, Settings


def metadata(path):
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
        values = dict(db.execute("SELECT key,value FROM meta"))
    return {key: values[key] for key in ("corpus_id", "version")}


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    path.chmod(0o600)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="New isolated directory, never production data")
    parser.add_argument(
        "--upload", action="store_true",
        help="Explicitly authorize metadata-only LangSmith upload with dedicated configured key",
    )
    args = parser.parse_args()
    output = args.output.expanduser().resolve()
    settings = Settings()
    fields = settings.model_fields_for_children()
    if not fields["llm_api_key"]:
        parser.error("Real model credentials are required; no simulated success")
    if args.upload and not settings.langsmith_api_key.get_secret_value():
        parser.error("--upload requires BL_LANGSMITH_API_KEY; never print keys")
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    adapters = Assistants(settings, "trace-isolation")
    sources = {
        kind: adapters.knowledge_settings(kind).data_dir / "knowledge.sqlite3"
        for kind in ("knowledge", "questions")
    }
    before = {kind: metadata(path) for kind, path in sources.items()}
    report = {
        "cloud": "requested" if args.upload else "not_run",
        "inputs": "technical questions and synthetic resume, no real career data",
        "production_before": before, "cases": {},
    }
    try:
        manifests = {
            kind: clone_corpus(adapters.knowledge_settings(kind), output / kind, kind)
            for kind in sources
        }
        write(output / "manifest.json", manifests)
        env = {
            **os.environ, "PYTHONDONTWRITEBYTECODE": "1",
            "BL_DATA_DIR": str(output / "runtime"), "BL_MEMORY_DIR": str(output / "memory"),
            "BL_TRACE_MODE": "langsmith" if args.upload else "local",
            "BL_LOCAL_ONLY": "true",
        }
        for name, value in fields.items():
            env["BL_" + name.upper()] = str(value)
        for kind, short in (("knowledge", "KA"), ("questions", "IQB")):
            env[f"BL_{short}_DATA_DIR"] = manifests[kind]["data_dir"]
            env[f"BL_{short}_NAMESPACE"] = manifests[kind]["namespace"]
        for name, question in (
            ("supervisor_knowledge",
             "先查看状态，然后明确调用一次 explain_knowledge，问题为"
             "“langchain-ai/langchain 的README列出哪些生态组件？”。"
             "只做这次知识查询，不修改条件、不创建其他任务。"),
            ("question_bank", "/questions RAG 检索增强生成的基本流程 --count 1"),
            ("resume_parser", f'/resume "{ROOT / "tests/fixtures/synthetic-resume.md"}"'),
        ):
            command = [
                str(ROOT / ".venv/bin/bl"), "chat", "--allow-remote",
                "--local-only", "--json", "-q", question,
            ]
            if args.upload:
                command.append("--allow-trace-upload")
            completed = subprocess.run(
                command, env=env, text=True, capture_output=True, timeout=600, cwd=ROOT,
            )
            if not completed.stdout:
                report["cases"][name] = {
                    "exit_code": completed.returncode, "error": "cli_no_json",
                }
                raise RuntimeError("Trace smoke CLI failed; no raw exception/body published")
            result = json.loads(completed.stdout)
            report["cases"][name] = {
                "exit_code": completed.returncode, "business_status": result["status"],
                "trace": result.get("trace"),
            }
            if "trace" not in result:
                raise RuntimeError("Expected a persisted metadata-only trace")
            trace = json.loads(Path(result["trace"]["path"]).read_text())
            report["cases"][name]["summary"] = summarize(trace)
            write(output / f"{name}-metadata.json", trace)
            required = {
                "supervisor_knowledge": {
                    "explain_knowledge", "assistant.knowledge", "models.assess", "reranker",
                },
                "question_bank": {
                    "assistant.questions", "models.parse", "models.draft", "models.verify",
                },
                "resume_parser": {"job.parse_resume", "models.structured"},
            }[name] | {"llm"}
            missing = sorted(required - {span["name"] for span in trace["runs"]})
            report["cases"][name]["missing_spans"] = missing
            if missing or trace["dropped_spans"]:
                raise RuntimeError("Trace coverage incomplete; inspect metadata report")
            if completed.returncode:
                raise RuntimeError("Trace smoke business step failed; inspect metadata report")
    finally:
        adapters.close()
        report["production_after"] = {kind: metadata(path) for kind, path in sources.items()}
        report["production_unchanged"] = report["production_after"] == before
        write(output / "report.json", report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["production_unchanged"]:
        raise RuntimeError("Production corpus changed during smoke; do not mark acceptance passed")


if __name__ == "__main__":
    main()
