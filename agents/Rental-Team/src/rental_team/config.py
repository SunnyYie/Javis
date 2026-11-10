import json
from pathlib import Path
from types import SimpleNamespace

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="RENTAL_", env_file=ROOT / ".env", extra="ignore",
    )
    data_dir: Path = ROOT / "data"
    # Deliberately independent of task/session/data_dir so restarts cannot reset usage.
    quota_path: Path = ROOT / "data" / "quota.sqlite3"
    vigo_api_key: SecretStr = SecretStr("")
    credentials_file: Path = ROOT / "credentials.json"
    vigo_daily_limit: int = Field(30, ge=1, le=30)
    vigo_task_limit: int = Field(5, ge=1, le=5)
    detail_limit: int = Field(3, ge=0, le=3)
    request_timeout: float = Field(35, gt=0, le=120)
    task_timeout: float = Field(180, gt=0, le=600)
    retention_days: int = Field(7, ge=1, le=90)
    ziroom_python: Path | None = ROOT / ".venv-ziroom" / (
        "Scripts/python.exe" if __import__("os").name == "nt" else "bin/python"
    )
    chrome_path: Path = Path(
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    )
    amap_api_key: SecretStr = SecretStr("")

    def model_post_init(self, _context):
        path = self.credentials_file
        fields = ("vigo_api_key", "amap_api_key")
        if any(not getattr(self, name).get_secret_value() for name in fields) and path.is_file():
            if path.stat().st_mode & 0o077:
                raise ValueError("credentials permissions must be 0600")
            data = json.loads(path.read_text())
            for name in fields:
                if not getattr(self, name).get_secret_value() and data.get(name):
                    setattr(self, name, SecretStr(data[name]))

    def provider_config(self, source):
        fields = {
            "vigolive": ("vigo_api_key", "request_timeout", "detail_limit"),
            "ziroom": ("ziroom_python", "chrome_path", "request_timeout", "detail_limit"),
            "amap": ("amap_api_key", "request_timeout"),
        }[source]
        return SimpleNamespace(**{name: getattr(self, name) for name in fields})
