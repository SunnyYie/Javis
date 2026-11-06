"""Read-only BOSS adapter. Extraction adapted from ohmyboss; see NOTICE."""

import time
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from filelock import FileLock, Timeout

from .cookies import save_private_state
from .jobs import boss_url, normalize_job
from .models import AgentError
from .privacy import redact

CITIES = {
    "北京": "101010100", "上海": "101020100", "天津": "101030100",
    "重庆": "101040100", "广州": "101280100", "深圳": "101280600",
    "杭州": "101210100", "成都": "101270100", "武汉": "101200100",
    "南京": "101190100", "苏州": "101190400", "西安": "101110100",
    "长沙": "101250100", "郑州": "101180100", "合肥": "101220100",
    "厦门": "101230200", "福州": "101230100", "济南": "101120100",
    "青岛": "101120200", "宁波": "101210400", "东莞": "101281600",
}

LIST_JS = """() => Array.from(document.querySelectorAll('li.job-card-box')).map(card => {
 const get = s => card.querySelector(s)?.textContent?.trim() || '';
 const name = card.querySelector('.job-name');
 const tags = Array.from(card.querySelectorAll('.tag-list li')).map(e=>e.textContent.trim());
 return {
   name: get('.job-name'), link: name?.getAttribute('href') || '',
   company: get('.company-name, .boss-name'),
   location: get('.job-area, .company-location'),
   salary: get('.salary'),
   experience: tags.find(t=>t.includes('年') || t.includes('经验') || t==='应届生') || '',
   education: tags.find(t=>/本科|硕士|博士|大专|学历不限|中专|高中/.test(t)) || ''
 };
}).filter(j => j.name)"""

DETAIL_JS = """() => {
 const get = s => document.querySelector(s)?.textContent?.trim() || '';
 return {
   salary: get('.info-primary .salary, .salary'),
   description: get('.job-sec-text, .job-detail-section .text, .text.fold-text'),
   companyAddress: get('.job-location .location-address, .location-address'),
   companyIntro: get('.job-detail-company .company-info-box'),
   companyName: get('.business-info-box .company-name')
 };
}"""


def page_problem(url: str, text: str) -> str | None:
    if url in ("about:blank", "chrome-error://chromewebdata/"):
        return "source_blank"
    path = urlsplit(url).path.lower()
    if any(t in path for t in ("captcha", "verify", "safe/")) or any(
        t in text for t in ("请完成安全验证", "访问异常", "请完成下方验证", "滑动验证", "账号存在异常")
    ):
        return "verification_required"
    if any(t in text for t in ("操作过于频繁", "访问过于频繁", "请求过于频繁", "今日访问已达上限")):
        return "rate_limited"
    if "login" in path or "请登录后查看" in text:
        return "login_required"
    return None


class BossProvider:
    def __init__(self, settings, directory: Path, emit=print):
        self.settings, self.directory, self.emit = settings, directory, emit
        self._last_request = 0.0
        self._active_job: dict | None = None
        self.navigation_events = []

    def navigate(self, page, url: str):
        delay = self.settings.page_delay - (time.monotonic() - self._last_request)
        if delay > 0:
            time.sleep(delay)
        self._last_request = time.monotonic()
        response = page.goto(url, wait_until="domcontentloaded", timeout=30000)
        self.navigation_events.append({
            "requested_path": urlsplit(url).path,
            "response_status": response.status if response else None,
            "final_url": page.url.split("?")[0],
        })
        if response and response.status == 429:
            raise AgentError("rate_limited", "BOSS 返回限流，已停止采集")
        if response and response.status in (401, 403):
            raise AgentError("access_denied", "BOSS 拒绝访问，已停止采集")
        if response and response.status >= 500:
            raise AgentError("source_unavailable", "BOSS 服务异常")
        page.wait_for_timeout(1500)
        problem = page_problem(page.url, page.locator("body").inner_text(timeout=10000))
        if problem:
            raise AgentError(problem, "BOSS 要求登录、安全验证或触发访问限制")

    def save_state(self, context):
        path = self.directory / "boss-storage-state.json"
        save_private_state(path, context.storage_state())

    def open_context(self, pw):
        browser = pw.chromium.launch(
            headless=False, channel=self.settings.browser_channel or None,
        )
        try:
            state_path = self.directory / "boss-storage-state.json"
            context = browser.new_context(
                storage_state=str(state_path) if state_path.exists() else None,
                accept_downloads=False, service_workers="block",
            )
            allowed = ("zhipin.com", "zhipincdn.com", "kanzhun.com", "bosszhipin.com")

            def route(request_route):
                parts = urlsplit(request_route.request.url)
                host = parts.hostname or ""
                if parts.scheme in ("data", "blob") or (
                    parts.scheme == "https"
                    and any(host == d or host.endswith("." + d) for d in allowed)
                    and parts.port in (None, 443)
                ):
                    request_route.continue_()
                else:
                    request_route.abort()

            context.route("**/*", route)
            return browser, context
        except BaseException:
            browser.close()
            raise

    def check_login(self) -> dict:
        """Visit only the homepage; never search or wait for manual login."""
        from playwright.sync_api import TimeoutError as BrowserTimeout
        from playwright.sync_api import sync_playwright

        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock = FileLock(str(self.settings.data_dir / ".boss-collection.lock"), timeout=0)
        try:
            with lock, sync_playwright() as pw:
                browser, context = self.open_context(pw)
                try:
                    page = context.new_page()
                    try:
                        self.navigate(page, "https://www.zhipin.com/")
                        if not self.logged_in(page):
                            raise AgentError("login_unverified", "未见明确的已登录页面标识")
                        self.save_state(context)
                        return {"login_verified": True, "jobs_collected": 0}
                    except AgentError as exc:
                        self.capture(page, exc.code)
                        return {
                            "login_verified": False, "issue": exc.code, "jobs_collected": 0,
                        }
                    except BrowserTimeout:
                        issue = (
                            "source_blank" if page.url == "about:blank" else "source_timeout"
                        )
                        self.capture(page, issue)
                        return {"login_verified": False, "issue": issue, "jobs_collected": 0}
                    except Exception:
                        self.capture(page, "browser_error")
                        return {
                            "login_verified": False, "issue": "browser_error", "jobs_collected": 0,
                        }
                finally:
                    context.close()
                    browser.close()
        except Timeout:
            raise AgentError("collection_busy", "另一个浏览器任务正在运行") from None
        except AgentError:
            raise
        except Exception:
            raise AgentError("browser_unavailable", "浏览器无法启动，请运行 ja doctor") from None

    def logged_in(self, page) -> bool:
        indicators = page.locator(
            '.nav-figure, .user-nav .user-name, a[ka="header-username"], '
            'a[href*="/web/geek/chat"]'
        )
        return any(indicators.nth(i).is_visible() for i in range(indicators.count()))

    def login(self, page):
        self.emit("请在独立浏览器中手动登录 BOSS；不会自动提交账号或验证码。")
        try:
            self.navigate(page, "https://www.zhipin.com/")
        except AgentError as exc:
            if exc.code != "login_required":
                raise
        if self.logged_in(page):
            self.save_state(page.context)
            return
        deadline = time.monotonic() + self.settings.login_timeout
        while time.monotonic() < deadline:
            page.wait_for_timeout(1000)
            text = page.locator("body").inner_text(timeout=10000)
            problem = page_problem(page.url, text)
            if problem == "source_blank":
                raise AgentError("source_blank", "站点返回或跳转为空白页，不能判为登录提示")
            if problem in ("verification_required", "rate_limited"):
                raise AgentError(problem, "出现安全验证或访问限制，请人工处理后重新查询")
            if self.logged_in(page):
                self.save_state(page.context)
                return
        raise AgentError("login_required", "等待手动登录超时；未获取实时岗位")

    def capture(self, page, issue: str):
        evidence = self.directory / "browser-evidence"
        evidence.mkdir(exist_ok=True, mode=0o700)
        report = {
            "issue": issue, "url": page.url.split("?")[0].split("#")[0],
            "navigation_events": self.navigation_events,
        }
        try:
            page.screenshot(path=str(evidence / "last-error.png"), timeout=5000)
        except Exception:
            pass
        try:
            report["title"] = page.title()
            report["text_excerpt"] = redact(
                page.locator("body").inner_text(timeout=2000)[:2000],
            )
        except Exception:
            report["page_content_unavailable"] = True
        save_private_state(evidence / "last-error.json", report)

    def collect(self, prefs, limit: int, on_job) -> list[str]:
        from playwright.sync_api import sync_playwright

        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        city_codes = {}
        for city in prefs.cities:
            normalized = city.removesuffix("市")
            if normalized not in CITIES:
                raise AgentError("unsupported_city", f"暂不支持城市编码：{city}")
            city_codes[normalized] = CITIES[normalized]
        lock = FileLock(str(self.settings.data_dir / ".boss-collection.lock"), timeout=0)
        issues = []
        try:
            with lock, sync_playwright() as pw:
                browser, context = self.open_context(pw)
                page = context.new_page()
                try:
                    self.login(page)
                    seen = set()
                    for keyword in prefs.roles:
                        for city, code in city_codes.items():
                            if len(seen) >= limit:
                                break
                            self.navigate(page, "https://www.zhipin.com/web/geek/job?" +
                                          urlencode({"query": keyword, "city": code}))
                            try:
                                page.locator("li.job-card-box").first.wait_for(timeout=12000)
                            except Exception:
                                body = page.locator("body").inner_text()
                                problem = page_problem(page.url, body)
                                if problem:
                                    raise AgentError(problem)
                                if any(t in body for t in ("暂无相关职位", "没有找到相关职位")):
                                    continue
                                raise AgentError("page_changed", "未发现岗位卡片或明确的空结果提示")
                            records = {}
                            for _ in range(3):
                                for raw in page.evaluate(LIST_JS):
                                    url = boss_url(raw.get("link", ""))
                                    if url not in seen and len(seen) + len(records) < limit:
                                        raw["link"] = url
                                        raw["keyword"], raw["search_city"] = keyword, city
                                        location = raw.get("location", "")
                                        raw["city"] = re_city(location)
                                        if url not in records:
                                            on_job(normalize_job(raw.copy(), "boss_live"))
                                        records[url] = raw
                                if len(seen) + len(records) >= limit:
                                    break
                                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                                page.wait_for_timeout(int(self.settings.page_delay * 1000))
                                body = page.locator("body").inner_text()
                                problem = page_problem(page.url, body)
                                if problem:
                                    raise AgentError(problem)
                            for url, raw in records.items():
                                seen.add(url)
                                self._active_job = raw
                                self.navigate(page, url)
                                details = page.evaluate(DETAIL_JS)
                                raw.update({k: v for k, v in details.items() if v})
                                if not raw.get("description"):
                                    issues.append("detail_incomplete:" + normalize_job(
                                        raw, "boss_live"
                                    ).id)
                                on_job(normalize_job(raw.copy(), "boss_live"))
                                self._active_job = None
                    self.save_state(context)
                except KeyboardInterrupt:
                    self.capture(page, "interrupted")
                    raise
                except AgentError as exc:
                    issues.append(exc.code)
                    self.capture(page, exc.code)
                except Exception:
                    issues.append("browser_error")
                    self.capture(page, "browser_error")
                finally:
                    context.close()
                    browser.close()
        except Timeout:
            raise AgentError("collection_busy", "另一个采集任务正在运行") from None
        except AgentError:
            raise
        except Exception:
            raise AgentError("browser_unavailable", "浏览器无法启动，请运行 ja doctor") from None
        return issues


def re_city(location: str) -> str:
    for city in sorted(CITIES, key=len, reverse=True):
        if location.startswith(city):
            return city
    return ""
