from pathlib import Path

from javis_tools.adapters import model_options
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = ROOT.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="BL_", env_file=ROOT / ".env", extra="ignore",
    )
    workspace: Path = WORKSPACE
    data_dir: Path = ROOT / "data"
    memory_dir: Path = WORKSPACE / "memory"
    skills_dir: Path = WORKSPACE / "skills"
    llm_env_file: Path = WORKSPACE / "agents" / "Knowledge-Assistant" / ".env"
    llm_api_key: SecretStr = SecretStr("")
    llm_base_url: str = ""
    llm_model: str = "glm-5.3-flashx"
    llm_timeout: float = Field(120, gt=0, le=600)
    tool_limit: int = Field(12, ge=1, le=30)
    local_only: bool = False
    embedding_batch_size: int | None = Field(None, ge=1, le=128)
    reranker_batch_size: int | None = Field(None, ge=1, le=32)
    inference_cache_size: int | None = Field(None, ge=0, le=256)
    resume_font_path: Path | None = None
    resume_font_index: int = Field(0, ge=0, le=10)
    ka_data_dir: Path | None = None
    ka_namespace: str | None = Field(None, pattern=r"^[a-z][a-z0-9_]{0,31}$")
    iqb_data_dir: Path | None = None
    iqb_namespace: str | None = Field(None, pattern=r"^[a-z][a-z0-9_]{0,31}$")

    def model_fields_for_children(self):
        external = model_options(self.llm_env_file) if self.llm_env_file.is_file() else {}
        result = {}
        for name in ("llm_api_key", "llm_base_url", "llm_model", "llm_timeout"):
            value = getattr(self, name)
            if isinstance(value, SecretStr):
                value = value.get_secret_value()
            result[name] = value if name in self.model_fields_set else external.get(name, value)
        result["llm_timeout"] = float(result["llm_timeout"])
        return result
