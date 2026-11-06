"""Continue a successful real single-question run; keep the five-question E2E separate."""

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from interview_question_bank.cache import PROMPT_VERSION
from interview_question_bank.config import ROOT, Settings


@pytest.mark.live
@pytest.mark.skipif(os.getenv("IQB_CACHE_LIVE") != "1", reason="requires completed real smoke run")
def test_real_success_threshold_cache_without_providers_and_version_invalidation():
    source = Path(os.environ["IQB_SMOKE_SOURCE_DIR"])
    original = json.loads((source / "02-single-question.stdout").read_text())
    assert original["sufficient"] and original["persisted"] and original["hot_count"] == 1
    assert len(original["questions"]) == 1
    manifest = json.loads((source / "environment.json").read_text())
    assert manifest.get("LLM_MODEL") == Settings().llm_model, "Model changed; run a fresh smoke test."
    assert manifest.get("PROMPT_VERSION") == PROMPT_VERSION, "Prompt changed; run a fresh smoke test."
    source_commands = [json.loads(line) for line in (source / "commands.jsonl").read_text().splitlines()]
    args = source_commands[-1]["args"]
    root = Path(os.environ["IQB_EVIDENCE_DIR"]) / "single-cache"
    root.mkdir(parents=True, exist_ok=False)
    (root / "source.json").write_text(json.dumps({"smoke_directory": str(source), **manifest}))
    env = dict(os.environ, **manifest)
    executable = str(ROOT / ".venv" / "bin" / "iqb")

    def run(label, command, override=None, expected_exit=0):
        started = time.monotonic()
        result = subprocess.run(
            [executable, *command], env=dict(env, **(override or {})), text=True,
            capture_output=True, timeout=600,
        )
        (root / f"{label}.stdout").write_text(result.stdout)
        (root / f"{label}.stderr").write_text(result.stderr)
        with (root / "commands.jsonl").open("a") as stream:
            stream.write(json.dumps({
                "label": label, "args": command, "exit_code": result.returncode,
                "seconds": round(time.monotonic() - started, 2),
            }, ensure_ascii=False) + "\n")
        print(f"{label}: exit={result.returncode}", flush=True)
        assert result.returncode == expected_exit, result.stdout + result.stderr
        return json.loads(result.stdout) if expected_exit == 0 else result.stderr

    for number in (2, 3):
        value = run(f"hot-{number}", args)
        assert value["route"] == "local" and value["sufficient"] and value["persisted"]
        assert value["hot_count"] == number
        assert value["cache_promoted"] == (number == 3)
    unavailable = {"LLM_API_KEY": "", "MILVUS_URI": "http://127.0.0.1:1"}
    cached = run("fourth-without-model-or-milvus", args, unavailable)
    assert cached["route"] == "cache" and cached["hot_count"] == 4
    assert cached["questions"] == value["questions"]
    run("new-source", [
        "ingest", "https://redis.io/docs/latest/operate/oss_and_stack/management/persistence/",
    ])
    error = run("invalidated-without-model", args, unavailable, expected_exit=1)
    assert "LLM_API_KEY" in error
