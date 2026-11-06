from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")
    data_dir: Path = ROOT / "data"
    llm_api_key: SecretStr = SecretStr("")
    llm_base_url: str = ""
    llm_model: str = "glm-5.3-flashx"
    llm_timeout: float = Field(120, gt=0)
    llm_call_deadline: float = Field(180, gt=0)
    embedding_model: str = "sentence-transformers/paraphrase-multilingual-mpnet-base-v2"
    model_cache_dir: Path | None = None
    reranker_enabled: bool = True
    reranker_model: str = "BAAI/bge-reranker-v2-m3-int8"
    retrieval_candidates: int = Field(48, ge=8, le=200)
    reranker_batch_size: int = Field(8, ge=1, le=32)
    milvus_uri: str = "http://127.0.0.1:19532"
    milvus_token: SecretStr = SecretStr("")
    index_namespace: str = Field("iqb", pattern=r"^[a-z][a-z0-9_]{0,31}$")
    redis_url: SecretStr = SecretStr("redis://127.0.0.1:16380/0")
    github_token: SecretStr = SecretStr("")
    tavily_api_key: SecretStr = SecretStr("")
    search_endpoint: str = "https://api.tavily.com/search"
    browser_enabled: bool = True
    cache_ttl: int = Field(86400, gt=0, le=86400)
    hot_window: int = Field(86400, gt=0)
    hot_threshold: int = Field(3, ge=1)
    repo_ttl: int = Field(604800, gt=0)
    web_ttl: int = Field(86400, gt=0)
    max_files: int = Field(60, gt=0)
    max_file_bytes: int = Field(262144, gt=0)
    max_repo_bytes: int = Field(10485760, gt=0)
    chunk_tokens: int = Field(384, gt=64, le=384)
    chunk_overlap: int = Field(64, ge=0, le=64)

    def model_settings(self):
        return {
            "api_key": self.llm_api_key.get_secret_value(),
            "base_url": self.llm_base_url,
            "model": self.llm_model,
            "timeout": self.llm_timeout,
        }

    def retrieval_settings(self):
        return {
            "version": "hybrid-rrf-crossencoder-v2",
            "candidates": self.retrieval_candidates,
            "reranker_enabled": self.reranker_enabled,
            "reranker_model": self.reranker_model,
        }


@lru_cache
def get_settings():
    return Settings()
