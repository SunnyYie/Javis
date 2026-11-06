"""Real Chromium on synthetic HTML; not evidence of current BOSS page selectors."""

import os

import pytest
from filelock import FileLock
from playwright.sync_api import sync_playwright

from job_agent.boss import DETAIL_JS, LIST_JS, BossProvider
from job_agent.config import Settings
from job_agent.cookies import normalize_cookies
from job_agent.models import AgentError

pytestmark = pytest.mark.skipif(
    os.getenv("JA_BROWSER_FIXTURES") != "1", reason="opt-in synthetic browser contract checks",
)


@pytest.fixture
def page():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        yield page
        browser.close()


def test_real_dom_extracts_synthetic_cards(page):
    page.set_content("""
        <ul><li class="job-card-box">
          <a class="job-name" href="/job_detail/synthetic.html?ka=test">合成前端岗位</a>
          <span class="company-name">合成测试公司</span>
          <span class="job-area">杭州·西湖区</span><span class="salary">20-30K·14薪</span>
          <ul class="tag-list"><li>3-5年</li><li>本科</li></ul>
        </li></ul>
    """)
    [job] = page.evaluate(LIST_JS)
    assert job == {
        "name": "合成前端岗位", "link": "/job_detail/synthetic.html?ka=test",
        "company": "合成测试公司", "location": "杭州·西湖区",
        "salary": "20-30K·14薪", "experience": "3-5年", "education": "本科",
    }


def test_real_dom_extracts_synthetic_details(page):
    page.set_content("""
        <div class="info-primary"><span class="salary">25-35K</span></div>
        <div class="job-sec-text">React TypeScript 合成岗位正文</div>
        <div class="job-location"><span class="location-address">合成办公地址</span></div>
    """)
    job = page.evaluate(DETAIL_JS)
    assert job["description"] == "React TypeScript 合成岗位正文"
    assert job["salary"] == "25-35K"
    assert job["companyAddress"] == "合成办公地址"


def test_visible_login_indicator_not_cookie_presence(page, tmp_path):
    provider = BossProvider(Settings(_env_file=None, data_dir=tmp_path), tmp_path)
    page.set_content('<a href="/web/geek/chat" style="display:none">沟通</a>')
    assert not provider.logged_in(page)
    page.set_content('<div class="nav-figure">合成已登录用户</div>')
    assert provider.logged_in(page)


def test_collection_lock_prevents_launch(tmp_path, prefs):
    settings = Settings(_env_file=None, data_dir=tmp_path)
    provider = BossProvider(settings, tmp_path / "session")
    with FileLock(str(tmp_path / ".boss-collection.lock")):
        with pytest.raises(AgentError, match="另一个采集"):
            provider.collect(prefs, 1, lambda job: None)


def test_imported_cookie_format_accepted_by_browser(page):
    cookies, _ = normalize_cookies([{
        "domain": ".zhipin.com", "name": "synthetic-only", "value": "not-a-real-token",
        "path": "/", "secure": True, "sameSite": "no_restriction", "session": True,
    }])
    page.context.add_cookies(cookies)
    [stored] = page.context.cookies("https://www.zhipin.com/")
    assert stored["value"] == "not-a-real-token"
    assert stored["sameSite"] == "None"
    assert stored["expires"] == -1
