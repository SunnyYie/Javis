import json
from datetime import date

import pytest

from job_agent.boss import page_problem
from job_agent.cache import Cache
from job_agent.config import Settings
from job_agent.models import AgentError, Evidence, WorkExperience
from job_agent.privacy import redact
from job_agent.resume import ResumeParser, months_worked, useful_text


@pytest.mark.parametrize("text", [
    "张三\n13800138000\ntest@example.com\n11010119900101001X",
    "张三前端工程师\n+86 13800138000\na.b@example.com",
    "姓名：张三\n微信：private-user\n住址：测试小区1号\n13800138000",
])
def test_redaction(text):
    result = redact(text)
    assert "13800138000" not in result
    assert "@example.com" not in result
    assert "张三" not in result
    assert "private-user" not in result
    assert "11010119900101001X" not in result


def work(start, end):
    return WorkExperience(
        company="虚构公司", role="开发", start=start, end=end,
        evidence=Evidence(page=1, quote="测试"),
    )


@pytest.mark.parametrize("items,expected", [
    ([("2024-08", "2025-10"), ("2025-11", "present")], 27),
    ([("2024-01", "2024-12"), ("2024-06", "2025-03")], 15),
    ([("2026-12", "present")], 0),
    ([("2024-01", "")], 0),
    ([("2025-01", "2024-01")], 0),
    ([], 0),
])
def test_work_month_union(items, expected):
    assert months_worked([work(a, b) for a, b in items], date(2026, 11, 5)) == expected


@pytest.mark.parametrize("url,text,expected", [
    ("https://www.zhipin.com/web/login", "", "login_required"),
    ("https://www.zhipin.com/", "请完成安全验证", "verification_required"),
    ("https://www.zhipin.com/", "访问过于频繁", "rate_limited"),
    ("https://www.zhipin.com/", "暂无相关职位", None),
    ("https://www.zhipin.com/", "前端工程师", None),
    ("about:blank", "", "source_blank"),
])
def test_source_failure_classification(url, text, expected):
    assert page_problem(url, text) == expected


def test_model_config_whitelist_precedence(tmp_path):
    external = tmp_path / "external.env"
    external.write_text(
        "LLM_MODEL=external\nLLM_API_KEY=private-key\nLLM_TIMEOUT=12\n"
        "TAVILY_API_KEY=not-authorized\nREDIS_URL=redis://evil:1\n", encoding="utf-8",
    )
    settings = Settings(_env_file=None, llm_env_file=external, llm_model="local")
    options = settings.model_options()
    assert options["model"] == "local"
    assert options["api_key"] == "private-key"
    assert options["timeout"] == 12
    assert "TAVILY_API_KEY" not in options
    assert settings.redis_url.get_secret_value() == "redis://127.0.0.1:16381/0"


def test_bad_files_and_blank_text(tmp_path, cache, models):
    parser = ResumeParser(Settings(_env_file=None, data_dir=tmp_path), cache, models)
    for name, text in [("empty.txt", ""), ("blank.md", "  "), ("bad.pdf", "not a pdf")]:
        path = tmp_path / name
        path.write_text(text)
        with pytest.raises(AgentError):
            parser.extract(path)
    assert not useful_text("�" * 100)


def test_plaintext_extraction_and_cache(tmp_path, cache, models):
    path = tmp_path / "synthetic.md"
    path.write_text("虚构活动工具链 React TypeScript 软件工程 本科 开发经历 " * 5)
    parser = ResumeParser(Settings(_env_file=None, data_dir=tmp_path), cache, models)
    assert parser.extract(path)[0].method == "text"
    assert parser.extract(path)[0].text == path.read_text()
    assert len(cache.data) == 1
    with pytest.raises(AgentError):
        parser.extract(path, force_ocr=True)


def test_redis_failure_visible_not_fatal(tmp_path):
    settings = Settings(_env_file=None, redis_url="redis://127.0.0.1:1/0")
    cache = Cache(settings, "offline-test")
    assert cache.get(cache.key("test", {})) is None
    cache.set("key", {})
    assert cache.warnings


def test_cache_namespace_isolates_runtime_directories(tmp_path):
    one = Cache(Settings(_env_file=None, data_dir=tmp_path / "one"), "same-session")
    two = Cache(Settings(_env_file=None, data_dir=tmp_path / "two"), "same-session")
    assert one.prefix != two.prefix
    assert one.key("ocr", {"content": "same"}) != two.key("ocr", {"content": "same"})


def test_tool_input_files_are_explicitly_authorized(service, tmp_path):
    other = tmp_path / "private.env"
    other.write_text("PRIVATE_KEY=not-authorized")
    with pytest.raises(AgentError, match="明确选择"):
        service.parse_resume(str(other))
    data = tmp_path / "synthetic.md"
    data.write_text("虚构简历 React TypeScript " * 5)
    authorized = service.authorize_file(data, "resume")
    assert authorized != data
    assert service.approved(str(authorized), "resume") == authorized
    with pytest.raises(AgentError):
        service.approved(str(authorized), "jobs")


def test_import_source_time_not_fabricated(tmp_path):
    from job_agent.jobs import import_jobs
    path = tmp_path / "synthetic.json"
    path.write_text(json.dumps([
        {"name": "虚构岗位", "link": "/job_detail/a.html"},
        {"name": "虚构岗位", "link": "/job_detail/a.html", "description": "more detail"},
        {"name": "非法链接", "link": "http://127.0.0.1/private"},
        None,
    ]))
    jobs, issues = import_jobs(path)
    assert len(jobs) == 1 and len(issues) == 2
    assert jobs[0].fetched_at is None
    assert jobs[0].imported_at is not None
    assert jobs[0].description == "more detail"
    assert jobs[0].source == "ohmyboss_import"
