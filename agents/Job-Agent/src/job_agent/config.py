from pathlib import Path

from dotenv import dotenv_values
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]
MODEL_KEYS = ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL", "LLM_TIMEOUT")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")
    data_dir: Path = ROOT / "data"
    llm_env_file: Path | None = None
    llm_api_key: SecretStr = SecretStr("")
    llm_base_url: str = ""
    llm_model: str = ""
    llm_timeout: float = Field(default=90, gt=0)
    llm_max_tokens: int = Field(default=8192, ge=1024)
    redis_url: SecretStr = SecretStr("redis://127.0.0.1:16381/0")
    cache_ttl: int = Field(default=86400, gt=0)
    browser_channel: str = ""
    page_delay: float = Field(default=3, ge=3)
    login_timeout: int = Field(default=180, ge=1, le=600)
    search_limit: int = Field(default=30, ge=1, le=30)

    def model_options(self) -> dict:
        external = {}
        if self.llm_env_file:
            if not self.llm_env_file.is_file():
                raise ValueError("LLM_ENV_FILE does not exist")
            external = {
                k: v for k, v in dotenv_values(self.llm_env_file).items() if k in MODEL_KEYS
            }
        result = {}
        for field in ("api_key", "base_url", "model", "timeout"):
            name = f"llm_{field}"
            value = getattr(self, name)
            if isinstance(value, SecretStr):
                value = value.get_secret_value()
            if name not in self.model_fields_set:
                value = external.get(name.upper()) or value
            result[field] = value
        result["timeout"] = float(result["timeout"])
        return result
