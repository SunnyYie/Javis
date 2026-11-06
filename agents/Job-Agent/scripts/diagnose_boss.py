"""Bounded homepage diagnostics; no search, model calls or credential logging."""

import argparse
import re
import signal
from collections import Counter
from urllib.parse import urlsplit

from filelock import FileLock
from playwright.sync_api import sync_playwright

from job_agent.boss import BossProvider, page_problem
from job_agent.config import ROOT, Settings
from job_agent.cookies import save_private_state
from job_agent.models import now
from job_agent.storage import Storage


class DiagnosticDeadline(BaseException):
    pass


def deadline(_signal, _frame):
    raise DiagnosticDeadline()


def endpoint(url):
    parts = urlsplit(url)
    return {"scheme": parts.scheme, "host": parts.hostname, "path": parts.path}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", required=True)
    args = parser.parse_args()
    settings = Settings()
    storage = Storage(settings.data_dir)
    storage.state(args.session)  # Do not silently create an unknown session.
    directory = storage.directory(args.session)
    stamp = now()[:19].replace(":", "-")
    evidence = ROOT / "data" / "acceptance" / ("boss-diagnostic-" + stamp)
    evidence.mkdir(parents=True, mode=0o700)
    provider = BossProvider(settings, directory)
    events = []
    responses = Counter()
    failures = Counter()

    def response_event(response):
        responses[(urlsplit(response.url).hostname, response.status)] += 1

    def failed(request):
        # Persist only the error code, never raw exceptions, headers or query strings.
        match = re.search(r"net::ERR_[A-Z_]+", request.failure or "")
        code = match.group() if match else "request_failed"
        failures[(urlsplit(request.url).hostname, code)] += 1
        if len(events) < 100:
            events.append({**endpoint(request.url), "failure": code})

    report = {
        "at": now(), "session": args.session, "purpose": "homepage-only network diagnostics",
        "login_verified": False, "jobs_collected": 0, "issue": None,
    }
    previous_handler = signal.signal(signal.SIGALRM, deadline)
    signal.alarm(60)
    try:
        with FileLock(str(settings.data_dir / ".boss-collection.lock"), timeout=0):
            with sync_playwright() as pw:
                browser, context = provider.open_context(pw)
                try:
                    page = context.new_page()
                    page.on("response", response_event)
                    page.on("requestfailed", failed)
                    page.on("framenavigated", lambda frame: events.append({
                        "navigation": endpoint(frame.url),
                    }) if frame == page.main_frame and len(events) < 100 else None)
                    try:
                        provider.navigate(page, "https://www.zhipin.com/")
                    except Exception as exc:
                        report["issue"] = getattr(exc, "code", "navigation_incomplete")
                        report["exception_type"] = type(exc).__name__
                    report["page"] = endpoint(page.url)
                    try:
                        body = page.locator("body").inner_text(timeout=2000)
                        report["document"] = {"bodyCharacters": len(body)}
                        problem = page_problem(page.url, body)
                        if problem:
                            report["issue"] = problem
                        elif body and provider.logged_in(page):
                            report["login_verified"] = True
                        elif not report["issue"]:
                            report["issue"] = "login_unverified"
                    except Exception:
                        report["document_unavailable"] = True
                    try:
                        page.screenshot(path=str(evidence / "page.png"), timeout=5000)
                        report["screenshot_saved"] = True
                    except Exception:
                        report["screenshot_saved"] = False
                finally:
                    context.close()
                    browser.close()
    except DiagnosticDeadline:
        report["issue"] = "diagnostic_timeout"
    except KeyboardInterrupt:
        report["issue"] = "interrupted"
    except Exception as exc:
        report["issue"] = getattr(exc, "code", "diagnostic_error")
        report["exception_type"] = type(exc).__name__
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous_handler)
        report["responses"] = [
            {"host": host, "status": status, "count": count}
            for (host, status), count in responses.items()
        ]
        report["failures"] = [
            {"host": host, "code": code, "count": count}
            for (host, code), count in failures.items()
        ]
        report["events"] = events
        save_private_state(evidence / "report.json", report)
    print(str(evidence / "report.json"))
    print({
        key: report.get(key) for key in (
            "login_verified", "jobs_collected", "issue", "document", "responses", "failures",
        )
    })
    return 0 if report["login_verified"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
