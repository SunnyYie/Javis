import runpy
import stat
from pathlib import Path

import pytest
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
configure = runpy.run_path(str(ROOT / "scripts/configure_from_env.py"))["configure"]


def test_provider_import_is_explicit_scoped_private_and_non_destructive(tmp_path, capsys):
    source, destination = tmp_path / "source.env", tmp_path / ".env"
    original = (
        "ANTHROPIC_AUTH_TOKEN=test-secret\n"
        "ANTHROPIC_BASE_URL=https://gateway.example.com\n"
        "TAVILY_API_KEY=test-search-secret\n"
        "UNRELATED_PASSWORD=must-not-import\n"
    )
    source.write_text(original)
    configure(source, destination, "verified-compatible-model")
    values = dotenv_values(destination)
    assert values["LLM_API_KEY"] == "test-secret"
    assert values["LLM_BASE_URL"] == "https://gateway.example.com/v1"
    assert values["LLM_MODEL"] == "verified-compatible-model"
    assert values["TAVILY_API_KEY"] == "test-search-secret"
    assert "UNRELATED_PASSWORD" not in values and "ANTHROPIC_AUTH_TOKEN" not in values
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    assert source.read_text() == original
    output = capsys.readouterr().out
    assert "test-secret" not in output and "test-search-secret" not in output
    with pytest.raises(FileExistsError):
        configure(source, destination, "other")
    assert dotenv_values(destination) == values


def test_provider_import_preserves_explicit_openai_settings(tmp_path):
    source, destination = tmp_path / "source.env", tmp_path / ".env"
    source.write_text(
        "LLM_API_KEY=test\nLLM_BASE_URL=https://example.com/custom/v1\n"
        "LLM_MODEL=explicit\nLLM_TIMEOUT=30\nGITHUB_TOKEN=test-github\n"
    )
    configure(source, destination, "fallback")
    values = dotenv_values(destination)
    assert values["LLM_BASE_URL"] == "https://example.com/custom/v1"
    assert values["LLM_MODEL"] == "explicit"
    assert values["LLM_TIMEOUT"] == "30" and values["GITHUB_TOKEN"] == "test-github"


def test_missing_model_config_does_not_create_output(tmp_path):
    source, destination = tmp_path / "source.env", tmp_path / ".env"
    source.write_text("TAVILY_API_KEY=only-search\n")
    with pytest.raises(ValueError):
        configure(source, destination, "model")
    assert not destination.exists()
