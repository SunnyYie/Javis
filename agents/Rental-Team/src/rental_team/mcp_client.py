import asyncio
import json
import os
from contextlib import AsyncExitStack

import httpx2
from javis_core.models import BrainError
from jsonschema import Draft202012Validator
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client

VIGO_TOOLS = {"search_houses", "get_house_detail", "search_by_commute"}
ZIROOM_TOOLS = {
    "create_search_session", "get_filter_schema", "search_location", "select_filter_option",
    "set_filter_range", "get_results", "get_listing_detail", "close_search_session",
}
BLOCKED = {
    "missing_key", "authentication_failed", "quota_exhausted", "rate_limited", "login_required",
    "captcha", "access_blocked", "not_configured", "unsupported_filter", "quota_unknown",
}


def failure_code(exc):
    if isinstance(exc, BrainError):
        return exc.code
    if isinstance(exc, TimeoutError):
        return "timeout"
    # Inspect only to classify; never persist third-party exception text.
    text = str(exc).casefold()
    nested = getattr(exc, "exceptions", [])
    for child in nested:
        code = failure_code(child)
        if code != "provider_error":
            return code
    for terms, code in [
        (("quota", "-32001", "配额", "额度"), "quota_exhausted"),
        (("401", "403", "api-key", "api key", "unauthorized", "鉴权"), "authentication_failed"),
        (("429", "rate limit", "限流"), "rate_limited"),
        (("captcha", "验证码"), "captcha"),
        (("login", "登录"), "login_required"),
        (("567", "access_blocked", "安全验证", "禁止访问"), "access_blocked"),
        (("timeout", "timed out"), "timeout"),
        (("version", "版本"), "version_conflict"),
        (("parse_error",), "parse_error"),
    ]:
        if any(term in text for term in terms):
            return code
    return "provider_error"


class MCP:
    """One task-owned connection; no sampling, roots or arbitrary execution tools."""

    def __init__(self, source, settings, budget):
        if source not in ("vigolive", "ziroom"):
            raise BrainError("source_not_allowed")
        self.source, self.settings, self.budget = source, settings, budget
        self.allowed = VIGO_TOOLS if source == "vigolive" else ZIROOM_TOOLS
        self.stack = AsyncExitStack()
        self.schemas = {}
        self.server_version = None

    async def __aenter__(self):
        try:
            if self.source == "vigolive":
                key = self.settings.vigo_api_key.get_secret_value()
                if not key:
                    raise BrainError("missing_key")
                # Conservatively account for initialization, notification and discovery.
                for _ in range(2):
                    await self.budget.before(self.source, business=False)
                client = await self.stack.enter_async_context(httpx2.AsyncClient(
                    headers={"X-API-Key": key}, timeout=self.settings.request_timeout,
                    follow_redirects=False,
                ))
                streams = await self.stack.enter_async_context(streamable_http_client(
                    "https://mcp.vigolive.cn/mcp/", http_client=client,
                    terminate_on_close=False,
                ))
            else:
                python = self.settings.ziroom_python
                if not python or not python.is_file():
                    raise BrainError("not_configured")
                # No inherited model/API keys, user browser directories, or fake backend.
                env = {k: os.environ[k] for k in ("PATH", "HOME", "TMPDIR") if k in os.environ}
                env.update({
                    "PYTHONPATH": str(__import__("pathlib").Path(__file__).resolve().parents[1]),
                    "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1",
                    "ZIROOM_BACKEND": "playwright",
                    "ZIROOM_CHROME_PATH": str(self.settings.chrome_path),
                })
                stderr = self.stack.enter_context(open(os.devnull, "w"))
                streams = await self.stack.enter_async_context(stdio_client(
                    StdioServerParameters(
                        command=str(python), args=["-m", "rental_team.ziroom_server"], env=env,
                    ), errlog=stderr,
                ))
            self.session = await self.stack.enter_async_context(ClientSession(
                *streams, read_timeout_seconds=self.settings.request_timeout,
            ))
            initialized = await self.session.initialize()
            self.server_version = initialized.server_info.version
            if self.source == "vigolive":
                await self.budget.before(self.source, business=False)
            listed = await self.session.list_tools()
            self.schemas = {t.name: t.input_schema for t in listed.tools if t.name in self.allowed}
            required = {"search_houses", "get_house_detail"} if self.source == "vigolive" else {
                "create_search_session", "get_filter_schema", "get_results",
                "get_listing_detail", "close_search_session", "set_filter_range",
            }
            if not required <= self.schemas.keys():
                raise BrainError("tool_contract_changed")
            return self
        except BaseException:
            await self.stack.aclose()
            raise

    async def __aexit__(self, *exc):
        await self.stack.aclose()

    async def call(self, name, arguments, *, cleanup=False):
        if name not in self.allowed or name not in self.schemas:
            raise BrainError("tool_not_allowed")
        if not Draft202012Validator(self.schemas[name]).is_valid(arguments):
            raise BrainError("tool_arguments_invalid")
        if not cleanup:
            await self.budget.before(self.source)
        async with asyncio.timeout(self.settings.request_timeout):
            result = await self.session.call_tool(name, arguments)
        if result.is_error:
            code = failure_code(Exception(str(result.content)))
            if code == "quota_exhausted" and self.source == "vigolive":
                self.budget.quota.exhaust()
            raise BrainError(code)
        data = result.structured_content
        if data is None:
            text = "".join(getattr(c, "text", "") for c in result.content)
            if len(text) > 1_000_000:
                raise BrainError("response_too_large")
            try:
                data = json.loads(text)
            except ValueError:
                raise BrainError(failure_code(Exception(text))) from None
        if not isinstance(data, dict):
            raise BrainError("tool_response_invalid")
        if len(json.dumps(data, ensure_ascii=False)) > 1_000_000:
            raise BrainError("response_too_large")
        if data.get("error"):
            raise BrainError(failure_code(Exception(str(data["error"]))))
        return data
