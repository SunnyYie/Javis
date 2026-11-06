"""Visible, read-only connectivity probe; never claims authenticated search acceptance."""
import argparse
import json
from pathlib import Path

from javis_tools.adapters import Assistants
from job_agent.boss import BossProvider
from job_agent.models import JobPreferences

from brain_leader.config import Settings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    settings = Settings(data_dir=output / "runtime")
    adapter = Assistants(settings, "browser-probe")
    job_settings = adapter.job.settings.model_copy(update={"login_timeout": 15})
    jobs = []
    try:
        issues = BossProvider(job_settings, output).collect(
            JobPreferences(roles=["前端"], cities=["北京"], target_monthly_k=25),
            1, jobs.append,
        )
    except Exception as exc:
        issues = [getattr(exc, "code", "browser_probe_failed")]
    report = {
        "purpose": "真实只读浏览器连接探针；测试条件不是用户求职偏好",
        "jobs_read": len(jobs), "issues": issues, "authenticated_search_e2e": False,
        "next_action": "页面可正常加载后，由用户以真实条件确认并手动登录，再验收搜索、匹配和导出。",
    }
    (output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
