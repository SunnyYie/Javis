"""A narrow real single-question probe, not a replacement for the five-question E2E."""

import json
import os
import subprocess
import time
import uuid
from pathlib import Path

import pexpect
import pytest

from interview_question_bank.cache import PROMPT_VERSION
from interview_question_bank.config import ROOT, Settings
from interview_question_bank.models import InterviewQuestion


@pytest.mark.live
@pytest.mark.skipif(os.getenv("IQB_SMOKE") != "1", reason="requires real model and services")
def test_real_single_question_and_terminal():
    settings = Settings()
    root = Path(os.environ["IQB_EVIDENCE_DIR"]) / "single"
    root.mkdir(parents=True, exist_ok=False)
    env = dict(
        os.environ, DATA_DIR=str(root / "knowledge"),
        INDEX_NAMESPACE="iqbsmoke_" + uuid.uuid4().hex[:8],
        MODEL_CACHE_DIR=str(settings.model_cache_dir or ROOT / "data" / "models"),
        LLM_MODEL=settings.llm_model,
    )
    manifest = {
        key: env[key] for key in ("DATA_DIR", "INDEX_NAMESPACE", "MODEL_CACHE_DIR", "LLM_MODEL")
    }
    (root / "environment.json").write_text(json.dumps(
        dict(manifest, PROMPT_VERSION=PROMPT_VERSION), indent=2,
    ))
    executable = str(ROOT / ".venv" / "bin" / "iqb")
    child = pexpect.spawn(executable, ["chat", "--local-only"], env=env,
                          encoding="utf-8", timeout=60)
    transcript = []
    try:
        child.expect(r"session ([0-9a-f]{12})")
        sid = child.match.group(1)
        child.expect("iqb> ")
        for command in ("/sessions", "/new", f"/resume {sid}", "/sources"):
            child.sendline(command)
            child.expect("iqb> ")
            transcript.append(child.before)
        child.sendline("/quit")
        child.expect(pexpect.EOF)
    finally:
        child.close(force=True)
        (root / "terminal.txt").write_text("\n".join(transcript))
    assert child.exitstatus == 0

    def run(label, *args):
        started = time.monotonic()
        result = subprocess.run(
            [executable, *args], env=env, text=True, capture_output=True, timeout=600,
        )
        (root / f"{label}.stdout").write_text(result.stdout)
        (root / f"{label}.stderr").write_text(result.stderr)
        with (root / "commands.jsonl").open("a") as stream:
            stream.write(json.dumps({
                "label": label, "args": args, "exit_code": result.returncode,
                "seconds": round(time.monotonic() - started, 2),
            }, ensure_ascii=False) + "\n")
        assert result.returncode == 0, result.stdout + result.stderr
        print(f"{label}: exit=0", flush=True)
        return json.loads(result.stdout)

    run("01-ingest", "ingest", "https://redis.io/docs/latest/develop/using-commands/pipelining/")
    answer = run(
        "02-single-question", "chat", "-q",
        "给我1道基础 Redis Pipeline 面试题，只讨论它如何减少网络往返次数。",
        "--local-only", "--json",
    )
    assert answer["route"] == "local" and answer["sufficient"] and answer["persisted"]
    assert len(answer["questions"]) == 1 and answer["citations"]
    question = InterviewQuestion.model_validate(answer["questions"][0])
    assert question.difficulty == "基础"
    assert set(question.evidence_ids) <= {item["id"] for item in answer["citations"]}
