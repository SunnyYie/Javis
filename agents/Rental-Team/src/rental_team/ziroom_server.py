"""Project-owned network boundary around the separately installed upstream server."""
import asyncio
import re
from urllib.parse import urlsplit


def allowed_url(url):
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    return parsed.scheme == "https" and (
        host == "ziroom.com" or host.endswith(".ziroom.com")
    ) and not parsed.username and not parsed.password


def main():
    from ziroom_mcp.backends import PlaywrightBackend
    from ziroom_mcp.server import create_mcp_server
    from ziroom_mcp.service import ZiroomService

    class GuardedBackend(PlaywrightBackend):
        async def _ensure_browser(self):
            await super()._ensure_browser()
            if getattr(self, "_guarded", False):
                return
            self._guarded = True
            original = self._browser.new_context

            async def new_context(*args, **kwargs):
                context = await original(*args, **kwargs)

                async def guard(route):
                    if allowed_url(route.request.url):
                        await route.continue_()
                    else:
                        await route.abort()

                await context.route("**/*", guard)
                return context

            self._browser.new_context = new_context

        async def validate(self, handle):
            page = handle.page
            if not allowed_url(page.url):
                raise ValueError("access_blocked")
            text = (await page.locator("body").inner_text())[:20000]
            if re.search(r"验证码|安全验证|访问过于频繁|禁止访问", text):
                raise ValueError("access_blocked")
            if re.search(r"请先登录|登录后查看", text):
                raise ValueError("login_required")
            if not await page.locator(".Z_list,.Z_list-box").count():
                raise ValueError("parse_error")
            return text

        async def schema(self, handle):
            await self.validate(handle)
            return await super().schema(handle)

        async def results(self, handle, limit):
            text = await self.validate(handle)
            result = await super().results(handle, limit)
            if not result["has_results"] and not re.search(r"暂无|没有找到|无匹配|0套", text):
                raise ValueError("parse_error")
            return result

        async def get_listing_detail(self, url, *args, **kwargs):
            if not allowed_url(url):
                raise ValueError("access_blocked")
            return await super().get_listing_detail(url, *args, **kwargs)

    backend = GuardedBackend()
    try:
        create_mcp_server(ZiroomService(backend)).run()
    finally:
        # Process exit also tears down all project-owned contexts and its browser.
        if backend._browser:
            try:
                asyncio.run(backend.shutdown())
            except RuntimeError:
                pass


if __name__ == "__main__":
    main()
