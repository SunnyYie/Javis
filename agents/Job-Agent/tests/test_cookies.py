import json
import stat
from contextlib import nullcontext
from unittest.mock import Mock

import pytest
from filelock import FileLock
from typer.testing import CliRunner

from job_agent.boss import BossProvider
from job_agent.cli import app
from job_agent.config import Settings
from job_agent.cookies import import_cookies, normalize_cookies
from job_agent.models import AgentError


def cookie(**changes):
    return {
        "domain": ".zhipin.com", "name": "synthetic-token", "value": "synthetic-secret",
        "path": "/", "secure": True, "httpOnly": True, "sameSite": "no_restriction",
        "expirationDate": 2000000000, "hostOnly": False, "storeId": "0", **changes,
    }


def test_editor_conversion_preserves_security_and_deduplicates():
    result, expired = normalize_cookies([
        cookie(value="old"), cookie(), cookie(name="expired", expirationDate=1),
    ], now=100)
    assert expired == 1
    assert result == [{
        "domain": ".zhipin.com", "name": "synthetic-token", "value": "synthetic-secret",
        "path": "/", "secure": True, "httpOnly": True, "sameSite": "None",
        "expires": 2000000000,
    }]


def test_session_host_only_and_native_state():
    [result], _ = normalize_cookies({"cookies": [
        cookie(hostOnly=True, session=True, sameSite="unspecified"),
    ], "origins": [{"origin": "https://untrusted.example", "localStorage": []}]})
    assert result["domain"] == "zhipin.com"
    assert result["expires"] == -1
    assert result["sameSite"] == "Lax"


def test_editor_null_same_site_uses_browser_default():
    [result], _ = normalize_cookies([cookie(sameSite=None, secure=False)])
    assert result["sameSite"] == "Lax"


@pytest.mark.parametrize("change", [
    {"domain": None}, {"domain": "evilzhipin.com"}, {"domain": ".zhipin.com.evil.test"},
    {"domain": "www..zhipin.com"}, {"domain": "https://www.zhipin.com"},
    {"secure": "true"}, {"httpOnly": 1}, {"value": None}, {"path": "not/absolute"},
    {"name": "bad\nname"}, {"expirationDate": float("nan")}, {"expirationDate": True},
    {"sameSite": "unknown"}, {"sameSite": "None", "secure": False},
])
def test_invalid_credentials_rejected_without_values(change):
    with pytest.raises(AgentError) as error:
        normalize_cookies([cookie(**change)])
    assert "synthetic-secret" not in str(error.value)


def test_import_private_no_origins_no_input_changes(tmp_path):
    source = tmp_path / "input.json"
    payload = json.dumps({"cookies": [cookie()], "origins": [{"secret": "ignored"}]})
    source.write_text(payload)
    directory = tmp_path / "session"
    report = import_cookies(source, directory, tmp_path)
    target = directory / "boss-storage-state.json"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert json.loads(target.read_text())["origins"] == []
    assert source.read_text() == payload
    assert report == {"imported": 1, "expired_skipped": 0, "login_verified": False}
    assert "synthetic-secret" not in json.dumps(report)
    with FileLock(str(tmp_path / ".boss-collection.lock")):
        with pytest.raises(AgentError, match="正在使用"):
            import_cookies(source, directory, tmp_path)


@pytest.mark.parametrize("payload", ["not-json", "x" * (1024 * 1024 + 1), "[]"])
def test_invalid_file_does_not_replace_state(tmp_path, payload):
    source = tmp_path / "input.json"
    source.write_text(json.dumps([cookie()]))
    directory = tmp_path / "session"
    import_cookies(source, directory, tmp_path)
    before = (directory / "boss-storage-state.json").read_bytes()
    source.write_text(payload)
    with pytest.raises(AgentError):
        import_cookies(source, directory, tmp_path)
    assert (directory / "boss-storage-state.json").read_bytes() == before


@pytest.mark.parametrize("issue", [None, "source_blank", "verification_required", "rate_limited"])
def test_login_probe_never_searches_or_waits(tmp_path, monkeypatch, issue):
    monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: nullcontext(Mock()))
    provider = BossProvider(Settings(_env_file=None, data_dir=tmp_path), tmp_path)
    browser, context, page = Mock(), Mock(), Mock()
    context.new_page.return_value = page
    provider.open_context = Mock(return_value=(browser, context))
    provider.navigate = Mock(side_effect=AgentError(issue) if issue else None)
    provider.logged_in = Mock(return_value=True)
    provider.save_state = Mock()
    provider.capture = Mock()
    provider.login = Mock(side_effect=AssertionError("must not wait for manual login"))
    result = provider.check_login()
    provider.navigate.assert_called_once_with(page, "https://www.zhipin.com/")
    assert result["jobs_collected"] == 0
    assert result["login_verified"] is (issue is None)
    if issue:
        assert result["issue"] == issue
        provider.save_state.assert_not_called()
    context.close.assert_called_once()
    browser.close.assert_called_once()


def test_auth_cli_help():
    runner = CliRunner()
    assert runner.invoke(app, ["auth", "import-cookies", "--help"]).exit_code == 0
    assert runner.invoke(app, ["auth", "check", "--help"]).exit_code == 0


def test_capture_persists_diagnostics_if_screenshot_fails(tmp_path):
    provider = BossProvider(Settings(_env_file=None, data_dir=tmp_path), tmp_path)
    page = Mock()
    page.url = "https://www.zhipin.com/?sensitive=not-for-logs"
    page.screenshot.side_effect = RuntimeError("screenshot unavailable")
    page.title.side_effect = RuntimeError("navigation interrupted")
    provider.capture(page, "source_timeout")
    report = json.loads((tmp_path / "browser-evidence/last-error.json").read_text())
    assert report["issue"] == "source_timeout"
    assert report["page_content_unavailable"]
    assert "sensitive" not in report["url"]


@pytest.mark.parametrize("url,issue", [
    ("about:blank", "source_blank"),
    ("https://www.zhipin.com/", "source_timeout"),
])
def test_probe_timeout_is_not_login_failure(tmp_path, monkeypatch, url, issue):
    from playwright.sync_api import TimeoutError

    monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: nullcontext(Mock()))
    provider = BossProvider(Settings(_env_file=None, data_dir=tmp_path), tmp_path)
    browser, context, page = Mock(), Mock(), Mock()
    page.url = url
    context.new_page.return_value = page
    provider.open_context = Mock(return_value=(browser, context))
    provider.navigate = Mock(side_effect=TimeoutError("not logged"))
    provider.capture = Mock()
    report = provider.check_login()
    assert report == {"login_verified": False, "issue": issue, "jobs_collected": 0}
    provider.capture.assert_called_once_with(page, issue)
