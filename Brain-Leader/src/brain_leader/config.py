from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from javis_tools.adapters import model_options
from pydantic import Field, SecretStr, field_validator
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
    trace_mode: Literal["off", "local", "langsmith"] = "off"
    langsmith_api_key: SecretStr = SecretStr("")
    langsmith_project: str = Field("brain-leader", pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    langsmith_endpoint: str = "https://api.smith.langchain.com"
    langsmith_workspace_id: UUID | None = None
    embedding_batch_size: int | None = Field(None, ge=1, le=128)
    reranker_batch_size: int | None = Field(None, ge=1, le=32)
    inference_cache_size: int | None = Field(None, ge=0, le=256)
    resume_font_path: Path | None = None
    resume_font_index: int = Field(0, ge=0, le=10)
    ka_data_dir: Path | None = None
    ka_namespace: str | None = Field(None, pattern=r"^[a-z][a-z0-9_]{0,31}$")
    iqb_data_dir: Path | None = None
    iqb_namespace: str | None = Field(None, pattern=r"^[a-z][a-z0-9_]{0,31}$")

    @field_validator("langsmith_endpoint")
    @classmethod
    def validate_trace_endpoint(cls, value):
        url = urlsplit(value)
        if (
            not url.hostname or url.username or url.password or url.query or url.fragment
            or (url.scheme != "https" and not (
                url.scheme == "http" and url.hostname in ("127.0.0.1", "localhost", "::1")
            ))
        ):
            raise ValueError("LangSmith endpoint must use HTTPS (HTTP only for loopback)")
        return value.rstrip("/")

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
