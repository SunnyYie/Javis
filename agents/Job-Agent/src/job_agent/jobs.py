import csv
import json
import re
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .models import AgentError, JobPosting, Salary, digest, now


def boss_url(value: str) -> str:
    value = value.strip()
    if value.startswith("/") and not value.startswith("//"):
        value = "https://www.zhipin.com" + value
    try:
        parsed = urlsplit(value)
    except ValueError:
        raise AgentError("invalid_job_url", "岗位链接格式无效") from None
    if (
        parsed.scheme != "https"
        or parsed.hostname != "www.zhipin.com"
        or parsed.username or parsed.password
        or parsed.netloc != "www.zhipin.com"
        or not re.fullmatch(r"/job_detail/[A-Za-z0-9_-]+\.html", parsed.path)
    ):
        raise AgentError("invalid_job_url", "仅接受 BOSS 官方 HTTPS 岗位详情链接")
    return urlunsplit(("https", "www.zhipin.com", parsed.path, "", ""))


def parse_salary(raw: str) -> Salary:
    salary = Salary(raw=raw)
    text = re.sub(r"\s+", "", raw).replace("－", "-").replace("–", "-")
    if not text or any(t in text for t in ("面议", "保密", "美元", "美金", "USD", "$", "港币")):
        return salary
    if any(t in text for t in ("元/天", "元/日", "元/时", "元/小时")):
        salary.period = "hour" if "时" in text else "day"
        salary.currency = "CNY"
        return salary
    if any(t in text for t in ("/年", "年薪", "万/年")):
        salary.period, salary.currency = "year", "CNY"
        return salary
    if "万" in text and "/月" not in text and "月薪" not in text:
        return salary
    match = re.fullmatch(
        r"(\d+(?:\.\d+)?)(?:-(\d+(?:\.\d+)?))?([kK千]|万)"
        r"(?:元)?(?:/月|月薪)?(?:[·•.](\d{1,2})薪)?", text
    )
    if match:
        low = float(match[1])
        high = float(match[2] or match[1])
        multiplier = 10 if match[3] == "万" else 1
        if 0 < low <= high:
            salary.min_k, salary.max_k = low * multiplier, high * multiplier
            salary.period, salary.currency = "month", "CNY"
            salary.months = int(match[4]) if match[4] else None
    return salary


def normalize_job(raw: dict, source: str, source_file: str | None = None) -> JobPosting:
    def text(*names):
        return next(
            (str(raw[n]).strip() for n in names if raw.get(n) not in (None, "")), ""
        )

    title = text("name", "title")
    if not title:
        raise AgentError("invalid_job_record", "岗位记录缺少 name/title")
    link = text("link", "url")
    url = boss_url(link) if link else ""
    description = text("description")
    observed = text("fetched_at", "collected_at")
    if observed:
        from datetime import datetime
        try:
            stamp = datetime.fromisoformat(observed.replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                observed = ""
        except ValueError:
            observed = ""
    return JobPosting(
        id=digest(url or {
            "title": title, "company": text("company", "companyName"),
            "city": text("city"), "location": text("location", "companyAddress"),
        })[:24],
        source=source,
        title=title, company=text("company", "companyName"),
        city=text("city"), location=text("location", "companyAddress"), url=url,
        salary=parse_salary(text("salary")), experience=text("experience"),
        education=text("education"), description=description,
        fetched_at=now() if source == "boss_live" else observed or None,
        imported_at=now() if source == "ohmyboss_import" else None,
        source_file=source_file,
        detail_complete=bool(description),
        raw=raw,
    )


def import_jobs(path: Path) -> tuple[list[JobPosting], list[str]]:
    path = path.expanduser().resolve()
    if not path.is_file() or path.stat().st_size > 20 * 1024 * 1024:
        raise AgentError("invalid_import", "岗位文件不存在或超过 20 MiB")
    try:
        if path.suffix.lower() == ".json":
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        elif path.suffix.lower() == ".csv":
            with path.open(encoding="utf-8-sig", newline="") as handle:
                data = list(csv.DictReader(handle))
        else:
            raise AgentError("unsupported_import", "岗位导入支持 JSON 和 CSV")
    except (ValueError, UnicodeError):
        raise AgentError("invalid_import", "岗位文件格式损坏") from None
    if not isinstance(data, list) or len(data) > 10000:
        raise AgentError("invalid_import", "岗位文件应为最多一万条记录的数组")
    jobs, issues = {}, []
    for i, record in enumerate(data, 1):
        if not isinstance(record, dict):
            issues.append(f"第 {i} 条不是对象，已跳过")
            continue
        try:
            job = normalize_job(record, "ohmyboss_import", str(path))
            previous = jobs.get(job.id)
            if previous is None or len(job.description) > len(previous.description):
                jobs[job.id] = job
        except AgentError as exc:
            issues.append(f"第 {i} 条：{exc.code}")
    return list(jobs.values()), issues
