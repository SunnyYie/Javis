"""Real website connectivity/login probe, NOT a completed job-search E2E."""

import json
from pathlib import Path

from job_agent.boss import BossProvider
from job_agent.config import ROOT, Settings
from job_agent.models import JobPreferences, now


def main():
    directory = ROOT / "data" / "acceptance" / ("boss-" + now()[:19].replace(":", "-"))
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    settings = Settings(login_timeout=15)
    prefs = JobPreferences(roles=["前端"], cities=["北京"], target_monthly_k=25)
    jobs = []
    try:
        issues = BossProvider(settings, directory).collect(prefs, 1, jobs.append)
    except Exception as exc:
        issues = [getattr(exc, "code", "browser_probe_failed")]
    report = {
        "at": now(), "purpose": "真实网站连接/登录状态探针；条件为测试输入，不是用户求职偏好",
        "jobs_read": len(jobs), "issues": issues,
        "authenticated_search_e2e": False,
        "next_action": (
            "先排查浏览器加载空白页；页面正常显示后由用户手动登录，再做真实条件验收"
            if "source_blank" in issues else
            "页面正常加载后由用户手动登录，以真实求职条件运行 ja chat 的 /search"
        ),
    }
    (directory / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(str(Path(directory).resolve()))


if __name__ == "__main__":
    main()
