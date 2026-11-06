from functools import lru_cache
from pathlib import Path
from typing import Literal

from dotenv import dotenv_values
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")
    data_dir: Path = ROOT / "data"
    llm_env_file: Path | None = None
    llm_api_key: SecretStr = SecretStr("")
    llm_base_url: str = ""
    llm_model: str = "glm-5.3-flashx"
    llm_timeout: float = Field(60, gt=0)
    embedding_model: str = "sentence-transformers/paraphrase-multilingual-mpnet-base-v2"
    model_cache_dir: Path | None = None
    rerank_enabled: bool = True
    reranker_model: str = "BAAI/bge-reranker-v2-m3-int8"
    rerank_candidates: int = Field(40, ge=8, le=40)
    embedding_batch_size: int = Field(16, ge=1, le=128)
    reranker_batch_size: int = Field(4, ge=1, le=32)
    inference_cache_size: int = Field(32, ge=0, le=256)
    readme_policy: Literal["canonical", "all"] = "canonical"
    hybrid_head: int = Field(1, ge=0, le=2)
    eval_max_tokens: int = Field(16384, ge=1024, le=65536)
    milvus_uri: str = "http://127.0.0.1:19531"
    milvus_token: SecretStr = SecretStr("")
    index_namespace: str = Field("ka", pattern=r"^[a-z][a-z0-9_]{0,31}$")
    redis_url: SecretStr = SecretStr("redis://127.0.0.1:16379/0")
    github_token: SecretStr = SecretStr("")
    github_import_mode: Literal["browser", "api"] = "browser"
    github_tree_pages: int = Field(12, ge=0, le=100)
    tavily_api_key: SecretStr = SecretStr("")
    search_endpoint: str = "https://api.tavily.com/search"
    browser_enabled: bool = True
    cache_ttl: int = Field(86400, gt=0, le=86400)
    repo_ttl: int = Field(604800, gt=0)
    web_ttl: int = Field(86400, gt=0)
    max_files: int = Field(60, gt=0)
    max_file_bytes: int = Field(262144, gt=0)
    max_repo_bytes: int = Field(10485760, gt=0)
    chunk_tokens: int = Field(384, gt=64, le=384)
    chunk_overlap: int = Field(64, ge=0, le=64)
    seeds_file: Path = ROOT / "seeds.json"

    def model_settings(self) -> dict:
        # External configuration is opt-in and limited to model settings.
        external = dotenv_values(self.llm_env_file) if self.llm_env_file else {}
        values = {
            "api_key": self.llm_api_key.get_secret_value() or external.get("LLM_API_KEY", ""),
            "base_url": self.llm_base_url or external.get("LLM_BASE_URL", ""),
            "model": self.llm_model,
            "timeout": self.llm_timeout,
        }
        if "llm_model" not in self.model_fields_set:
            values["model"] = external.get("LLM_MODEL", self.llm_model)
        if "llm_timeout" not in self.model_fields_set:
            values["timeout"] = float(external.get("LLM_TIMEOUT", self.llm_timeout))
        return values


@lru_cache
def get_settings() -> Settings:
    return Settings()
