"""Explicit, local-only import of user-supplied BOSS cookies."""

import json
import math
import os
import tempfile
import time
from pathlib import Path

from filelock import FileLock, Timeout

from .models import AgentError


def normalize_cookies(payload, now: float | None = None) -> tuple[list[dict], int]:
    records = payload.get("cookies") if isinstance(payload, dict) else payload
    if not isinstance(records, list) or not 1 <= len(records) <= 500:
        raise AgentError("invalid_cookies", "需要包含 1–500 项 Cookie 的 JSON 数组")
    now = time.time() if now is None else now
    cookies = {}
    expired = 0
    for record in records:
        if not isinstance(record, dict):
            raise AgentError("invalid_cookies", "Cookie 项格式错误")
        domain = record.get("domain")
        if not isinstance(domain, str):
            raise AgentError("invalid_cookies", "Cookie 域名格式错误")
        domain = domain.lower()
        if not (
            domain.lstrip(".") == "zhipin.com" or
            domain.lstrip(".").endswith(".zhipin.com")
        ) or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789.-" for c in domain) or any(
            not label or label.startswith("-") or label.endswith("-") or len(label) > 63
            for label in domain.removeprefix(".").split(".")
        ) or len(domain) > 254:
            raise AgentError("invalid_cookies", "仅允许 BOSS zhipin.com 域名")
        for key in ("hostOnly", "secure", "httpOnly", "session"):
            if key in record and not isinstance(record[key], bool):
                raise AgentError("invalid_cookies", "Cookie 布尔字段错误")
        if record.get("hostOnly"):
            domain = domain.lstrip(".")
        name, value, path = record.get("name"), record.get("value"), record.get("path", "/")
        if (
            not isinstance(name, str) or not name or
            not isinstance(value, str) or not isinstance(path, str) or
            not path.startswith("/") or any(
                ord(c) < 32 or ord(c) == 127 for c in name + value + path
            )
        ):
            raise AgentError("invalid_cookies", "Cookie 名称、值或路径错误")
        expires = record.get("expirationDate", record.get("expires", -1))
        if (
            isinstance(expires, bool) or not isinstance(expires, (float, int)) or
            not math.isfinite(expires)
        ):
            raise AgentError("invalid_cookies", "Cookie 有效期错误")
        if record.get("session"):
            expires = -1
        if expires != -1 and expires <= now:
            expired += 1
            continue
        same_site = record.get("sameSite")
        if same_site is None:
            same_site = "Lax"
        mapping = {
            "strict": "Strict", "lax": "Lax", "none": "None",
            "no_restriction": "None", "unspecified": "Lax",
        }
        if not isinstance(same_site, str) or same_site.lower() not in mapping:
            raise AgentError("invalid_cookies", "Cookie sameSite 字段错误")
        same_site = mapping[same_site.lower()]
        if same_site == "None" and not record.get("secure", False):
            raise AgentError("invalid_cookies", "SameSite=None 的 Cookie 必须启用 secure")
        cookies[(domain, path, name)] = {
            "domain": domain, "path": path, "name": name, "value": value,
            "expires": expires, "httpOnly": record.get("httpOnly", False),
            "secure": record.get("secure", False), "sameSite": same_site,
        }
    if not cookies:
        raise AgentError("invalid_cookies", "没有可导入的未过期 Cookie")
    return list(cookies.values()), expired


def save_private_state(path: Path, state: dict):
    """Atomically replace credentials without a world-readable creation window."""
    descriptor, temporary = tempfile.mkstemp(prefix=".boss-state-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(state, stream, ensure_ascii=False)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def import_cookies(path: Path, directory: Path, data_dir: Path) -> dict:
    try:
        with path.open("rb") as stream:
            raw = stream.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise AgentError("invalid_cookies", "Cookie 文件不能超过 1 MiB")
        payload = json.loads(raw)
        cookies, expired = normalize_cookies(payload)
    except AgentError:
        raise
    except Exception:
        raise AgentError("invalid_cookies", "无法解析 Cookie 文件，未输出原始内容") from None
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        with FileLock(str(data_dir / ".boss-collection.lock"), timeout=0):
            save_private_state(directory / "boss-storage-state.json", {
                "cookies": cookies, "origins": [],
            })
    except Timeout:
        raise AgentError("collection_busy", "浏览器正在使用登录态，不能替换") from None
    return {"imported": len(cookies), "expired_skipped": expired, "login_verified": False}
