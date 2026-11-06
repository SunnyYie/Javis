import importlib.util
import json
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import Mock

import pytest

from job_agent.config import Settings
from job_agent.models import AgentError
from job_agent.storage import Storage

spec = importlib.util.spec_from_file_location(
    "diagnose_boss", Path(__file__).resolve().parents[1] / "scripts/diagnose_boss.py",
)
diagnostics = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diagnostics)


def test_endpoint_omits_credentials_query_fragment():
    assert diagnostics.endpoint("https://user:secret@www.zhipin.com/?token=secret#secret") == {
        "scheme": "https", "host": "www.zhipin.com", "path": "/",
    }


@pytest.mark.parametrize("interruption", [None, diagnostics.DiagnosticDeadline, KeyboardInterrupt])
def test_diagnostics_keep_private_report_on_failure(tmp_path, monkeypatch, interruption):
    settings = Settings(_env_file=None, data_dir=tmp_path / "private")
    Storage(settings.data_dir).session("synthetic")
    monkeypatch.setattr(diagnostics, "ROOT", tmp_path)
    monkeypatch.setattr(diagnostics, "Settings", lambda: settings)
    monkeypatch.setattr("sys.argv", ["diagnose", "--session", "synthetic"])
    monkeypatch.setattr(diagnostics.signal, "alarm", Mock())
    monkeypatch.setattr(diagnostics.signal, "signal", Mock())
    monkeypatch.setattr(diagnostics, "sync_playwright", lambda: nullcontext(Mock()))
    browser, context, page, provider = Mock(), Mock(), Mock(), Mock()
    provider.open_context.return_value = browser, context
    provider.navigate.side_effect = AgentError("verification_required")
    provider.logged_in.return_value = False
    context.new_page.return_value = page
    page.url = "https://www.zhipin.com/?token=must-not-log"
    page.locator.return_value.inner_text.return_value = "请完成安全验证"
    if interruption:
        page.locator.return_value.inner_text.side_effect = interruption()
    page.screenshot.side_effect = RuntimeError("unavailable")
    monkeypatch.setattr(diagnostics, "BossProvider", lambda *args: provider)
    assert diagnostics.main() == 2
    [path] = (tmp_path / "data/acceptance").glob("*/report.json")
    payload = path.read_text()
    report = json.loads(payload)
    expected = (
        "verification_required" if interruption is None else
        "diagnostic_timeout" if interruption is diagnostics.DiagnosticDeadline else "interrupted"
    )
    assert report["issue"] == expected
    assert report["jobs_collected"] == 0
    assert not report["login_verified"]
    assert "must-not-log" not in payload
    assert "请完成安全验证" not in payload
    provider.navigate.assert_called_once_with(page, "https://www.zhipin.com/")
    context.close.assert_called_once()
    browser.close.assert_called_once()
