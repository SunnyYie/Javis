import pytest

from job_agent.jobs import boss_url, normalize_job, parse_salary
from job_agent.matching import match_job, rule_check
from job_agent.models import AgentError


def job(**changes):
    return normalize_job({
        "name": "前端开发", "company": "虚构测试公司", "city": "杭州",
        "salary": "25-35K·14薪", "experience": "经验不限",
        "description": "React TypeScript 前端开发，本科",
        "link": "/job_detail/synthetic.html", **changes,
    }, "ohmyboss_import")


@pytest.mark.parametrize("raw,low,high,period", [
    ("20-30K", 20, 30, "month"),
    ("15-30K·14薪", 15, 30, "month"),
    ("25K", 25, 25, "month"),
    ("2.5-3.5万/月", 25, 35, "month"),
    ("20－30k", 20, 30, "month"),
    ("200元/天", None, None, "day"),
    ("100元/时", None, None, "hour"),
    ("30-50万/年", None, None, "year"),
    ("面议", None, None, "unknown"),
    ("", None, None, "unknown"),
    ("20-30K美元", None, None, "unknown"),
    ("30-20K", None, None, "unknown"),
    ("50万", None, None, "unknown"),
])
def test_salary(raw, low, high, period):
    parsed = parse_salary(raw)
    assert (parsed.min_k, parsed.max_k, parsed.period) == (low, high, period)
    assert parsed.raw == raw


@pytest.mark.parametrize("url", [
    "http://www.zhipin.com/job_detail/test.html",
    "https://www.zhipin.com.evil.invalid/job_detail/test.html",
    "https://127.0.0.1/job_detail/test.html",
    "https://www.zhipin.com:443/job_detail/test.html",
    "https://user:pass@www.zhipin.com/job_detail/test.html",
    "javascript:alert(1)", "//evil.invalid/job_detail/test.html",
    "https://www.zhipin.com/web/geek/chat",
    "https://www.zhipin.com/job_detail/../other.html",
])
def test_reject_bad_url(url):
    with pytest.raises(AgentError):
        boss_url(url)


def test_canonical_url_and_dedupe():
    assert boss_url("/job_detail/test.html?ka=tracking") == (
        "https://www.zhipin.com/job_detail/test.html"
    )
    assert job(link="/job_detail/test.html?a=1").id == job(
        link="https://www.zhipin.com/job_detail/test.html#hash"
    ).id


def test_rules_city_salary_experience(profile, prefs):
    assert rule_check(job(city="北京"), prefs, profile)["reject"]
    assert rule_check(job(salary="15-20K"), prefs, profile)["reject"]
    overlap = rule_check(job(salary="20-30K", experience="3-5年"), prefs, profile)
    assert not overlap["reject"]
    assert len(overlap["gaps"]) == 2


def test_unknown_salary_not_a_pass(profile, prefs, models, cache):
    result = match_job(job(salary="面议"), prefs, profile, models, cache)
    assert result.level == "信息不足"
    assert result.pending


def test_rules_override_model(profile, prefs, models, cache):
    assert match_job(job(city="北京"), prefs, profile, models, cache).level == "不符合"
    assert models.calls == 0
    assert match_job(job(salary="20-30K"), prefs, profile, models, cache).level == "可尝试"
    assert match_job(job(experience="3-5年"), prefs, profile, models, cache).level == "可尝试"


def test_no_default_exclusion(profile, prefs):
    candidate = job(company="某初创外包", description="React TypeScript 单休，天使轮0-20人")
    assert not rule_check(candidate, prefs, profile)["reject"]
    prefs.excluded_terms = ["单休"]
    assert rule_check(candidate, prefs, profile)["reject"]


def test_missing_jd_does_not_call_llm(profile, prefs, models, cache):
    assert match_job(job(description=""), prefs, profile, models, cache).level == "信息不足"
    assert models.calls == 0


@pytest.mark.parametrize("failure", ["fail", "bad_evidence"])
def test_model_errors_are_not_partial_matches(profile, prefs, models, cache, failure):
    setattr(models, failure, True)
    result = match_job(job(), prefs, profile, models, cache)
    assert result.level == "评估失败"
    assert not cache.data
    assert "intentional" not in result.model_dump_json()


def test_match_cache_isolation(profile, prefs, models, cache):
    candidate = job()
    first = match_job(candidate, prefs, profile, models, cache)
    second = match_job(candidate, prefs, profile, models, cache)
    assert first.level == "满足条件"
    assert not first.cached and second.cached and models.calls == 1
    prefs.target_monthly_k = 26
    assert not match_job(candidate, prefs, profile, models, cache).cached
    assert models.calls == 2
    candidate.description += " Node.js"
    match_job(candidate, prefs, profile, models, cache)
    assert models.calls == 3
    profile.summary += " changed"
    match_job(candidate, prefs, profile, models, cache)
    assert models.calls == 4
