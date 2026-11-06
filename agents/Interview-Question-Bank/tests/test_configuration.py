import importlib.util
import stat
from pathlib import Path

import pytest
from dotenv import dotenv_values

from interview_question_bank.config import ROOT, Settings

spec = importlib.util.spec_from_file_location(
    "configure_iqb", ROOT / "scripts" / "configure_from_env.py",
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_authorized_import_is_allowlisted_private_and_non_overwriting(tmp_path, capsys):
    source, target = tmp_path / "source.env", tmp_path / ".env"
    source.write_text(
        "LLM_API_KEY='test-secret'\nLLM_MODEL='test-model'\n"
        "TAVILY_API_KEY='test-search'\nOTHER_SECRET='never-import'\n"
    )
    module.configure(source, target)
    values = dotenv_values(target)
    assert values["LLM_API_KEY"] == "test-secret"
    assert values["LLM_MODEL"] == "glm-5.3-flashx"
    assert "OTHER_SECRET" not in values
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert "test-secret" not in capsys.readouterr().out
    with pytest.raises(FileExistsError):
        module.configure(source, target)


def test_import_requires_actual_model_configuration(tmp_path):
    source = tmp_path / "source.env"
    source.write_text("OTHER_SECRET='not-a-model'\n")
    with pytest.raises(ValueError):
        module.configure(source, tmp_path / ".env")
    assert not (tmp_path / ".env").exists()


def test_project_defaults_and_validation():
    settings = Settings(_env_file=None)
    assert settings.llm_model == "glm-5.3-flashx"
    assert settings.milvus_uri.endswith(":19532")
    assert ":16380/" in settings.redis_url.get_secret_value()
    assert settings.index_namespace == "iqb"
    with pytest.raises(ValueError):
        Settings(_env_file=None, index_namespace="unsafe-name")
    with pytest.raises(ValueError):
        Settings(_env_file=None, cache_ttl=86401)


def test_root_is_absolute_not_launch_directory():
    assert isinstance(ROOT, Path) and ROOT.is_absolute()
