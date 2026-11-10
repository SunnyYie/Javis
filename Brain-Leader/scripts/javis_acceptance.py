"""Opt-in real terminal/model/provider acceptance; never substitutes fixtures for failures."""
import argparse
import json
import os
import sys
import time
from pathlib import Path

import pexpect


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--live-model", action="store_true")
    parser.add_argument("--live-sources", action="store_true")
    arguments = parser.parse_args()
    output = arguments.output.expanduser().resolve()
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    root = Path(__file__).resolve().parents[1]
    binary = Path(sys.executable).parent / "javis"
    env = {
        **os.environ, "BL_DATA_DIR": str(output / "brain"),
        "BL_MEMORY_DIR": str(output / "memory"), "BL_TRACE_MODE": "off",
        "BL_LLM_TIMEOUT": "60", "BL_TOOL_LIMIT": "2",
        "RENTAL_DATA_DIR": str(output / "rental"), "RENTAL_DETAIL_LIMIT": "1",
        "RENTAL_TASK_TIMEOUT": "180", "PYTHONDONTWRITEBYTECODE": "1",
    }
    if not arguments.live_sources:
        env.update(
            RENTAL_CREDENTIALS_FILE=str(output / "absent-credentials"),
            RENTAL_VIGO_API_KEY="", RENTAL_ZIROOM_PYTHON=str(output / "absent-python"),
        )
    report = {
        "kind": "real_terminal_acceptance",
        "live_model": arguments.live_model, "live_sources": arguments.live_sources,
        "fixture_notice": "北京/10000/整租/2026-12-01 是接口验收输入，不是用户实际租房需求",
        "budget": {"structured_operations": 2, "max_business_calls": 2,
                   "detail_limit_per_source": 1, "source_task_seconds": 180,
                   "note": "模型内部最多一次重试；SDK传输重试沿用原模型配置，不承诺货币费用"},
        "steps": [], "checks": {},
    }

    def invoke(question, *, remote=False):
        started = time.monotonic()
        args = ["chat", "-s", "javis-acceptance", "-q", question, "--json"]
        if remote:
            args.append("--allow-remote")
        child = pexpect.spawn(str(binary), args, cwd=str(root), env=env, encoding="utf-8",
                              timeout=240, echo=False, dimensions=(50, 200))
        try:
            child.expect(pexpect.EOF)
            raw = child.before
        finally:
            child.close(force=True)
        result, _ = json.JSONDecoder().raw_decode(raw[raw.index("{"):])
        report["steps"].append({
            "command": question if question.startswith("/") else "natural_rental_request",
            "exit_code": child.exitstatus, "seconds": round(time.monotonic() - started, 2),
            "result": result,
        })
        return result

    try:
        result = invoke("/rental search")
        report["checks"]["no_search_before_confirmation"] = (
            result["status"] == "blocked" and result["error"] == "requirements_confirmation_required"
        )
        if arguments.live_model:
            result = invoke(
                "帮我找北京月租不超过10000元的整租，2026年12月1日入住；预算仅房租。",
                remote=True,
            )
        else:
            result = invoke('/rental requirements {"city":"北京","rent_max":10000,'
                            '"rent_type":"entire","move_in":"2026-12-01"}')
        data = result["data"]
        proposal = data.get("data", data)
        report["checks"]["versioned_requirements_presented"] = (
            proposal.get("version") == 1
            and proposal.get("requirements", {}).get("rent_max") == 10000
            and proposal.get("confirmed_version") is None
        )
        result = invoke("/rental confirm 1")
        report["checks"]["human_confirmation"] = result["data"].get("confirmed_version") == 1
        if arguments.live_sources:
            result = invoke("/rental search")
            coverage = result.get("coverage", {})
            report["checks"]["both_sources_reported"] = set(coverage) == {"vigolive", "ziroom"}
            for source in ("vigolive", "ziroom"):
                report["checks"][source + "_list_and_detail"] = any(
                    row["listing"]["source"] == source
                    and row["listing"]["detail_status"] == "read"
                    for group in ("candidates", "needs_verification", "excluded")
                    for row in result.get("data", {}).get(group, [])
                )
            report["checks"]["three_expert_results"] = len(result.get("children", [])) == 3
    except Exception as exc:
        report["error"] = type(exc).__name__
        report["checks"]["execution_completed"] = False
    report["passed"] = all(report["checks"].values())
    path = output / "report.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    path.chmod(0o600)
    print(json.dumps({
        "report": str(path), "checks": report["checks"], "passed": report["passed"],
    }, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
