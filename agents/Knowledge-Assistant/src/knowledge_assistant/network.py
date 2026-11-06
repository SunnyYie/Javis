"""Bounded HTTP with DNS pinning, including redirects and browser subrequests."""

import asyncio
import http.client
import ipaddress
import json
import socket
import ssl
from urllib.parse import quote, urljoin, urlsplit

from .models import ServiceError


def public_target(url: str) -> tuple[str, int, str]:
    p = urlsplit(url)
    if p.scheme not in {"http", "https"} or not p.hostname or p.username or p.password:
        raise ServiceError("network", "unsafe_url")
    port = p.port or (443 if p.scheme == "https" else 80)
    if port not in {80, 443}:
        raise ServiceError("network", "unsafe_port")
    try:
        addresses = socket.getaddrinfo(p.hostname, port, type=socket.SOCK_STREAM)
        ips = list(dict.fromkeys(a[4][0] for a in addresses))
        if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
            raise ServiceError("network", "private_address_blocked")
        return p.hostname, port, ips[0]
    except (socket.gaierror, ValueError) as exc:
        raise ServiceError("network", "dns_error") from exc


class SafeHTTP:
    def request(
        self,
        method: str,
        url: str,
        *,
        headers=None,
        payload=None,
        max_bytes=2 * 1024 * 1024,
        redirects=4,
        timeout=30,
    ) -> tuple[bytes, dict, str]:
        headers = {"User-Agent": "Knowledge-Assistant/0.1", **(headers or {})}
        for _ in range(redirects + 1):
            host, port, ip = public_target(url)
            p = urlsplit(url)
            conn = None
            try:
                raw = socket.create_connection((ip, port), timeout=timeout)
                if p.scheme == "https":
                    conn = http.client.HTTPSConnection(host, port, timeout=timeout)
                    conn.sock = ssl.create_default_context().wrap_socket(raw, server_hostname=host)
                else:
                    conn = http.client.HTTPConnection(host, port, timeout=timeout)
                    conn.sock = raw
                data = json.dumps(payload).encode() if payload is not None else None
                request_headers = dict(headers)
                if data is not None:
                    request_headers["Content-Type"] = "application/json"
                request_path = quote(p.path, safe="/%:@!$&'()*+,;=-._~") or "/"
                if p.query:
                    request_path += "?" + quote(p.query, safe="/?%:@!$&'()*+,;=-._~")
                conn.request(
                    method,
                    request_path,
                    body=data,
                    headers=request_headers,
                )
                response = conn.getresponse()
                result_headers = {k.lower(): v for k, v in response.getheaders()}
                if response.status in {301, 302, 303, 307, 308}:
                    target = urljoin(url, result_headers.get("location", ""))
                    if urlsplit(target).netloc != p.netloc:
                        headers = {
                            k: v
                            for k, v in headers.items()
                            if k.lower() not in {"authorization", "cookie"}
                        }
                    if method != "GET":
                        raise ServiceError("network", "credential_redirect_blocked")
                    url = target
                    continue
                if response.status == 429 or (
                    response.status == 403
                    and (
                        result_headers.get("x-ratelimit-remaining") == "0"
                        or "retry-after" in result_headers
                    )
                ):
                    raise ServiceError(host, "rate_limited")
                if response.status in {401, 403}:
                    raise ServiceError(host, "permission_denied")
                if response.status == 404:
                    raise ServiceError(host, "not_found")
                if response.status >= 400:
                    raise ServiceError(host, f"http_{response.status}")
                body = response.read(max_bytes + 1)
                if len(body) > max_bytes:
                    raise ServiceError(host, "size_limit")
                return body, result_headers, url
            except (OSError, http.client.HTTPException) as exc:
                raise ServiceError(host, "connection_failed") from exc
            finally:
                if conn:
                    conn.close()
        raise ServiceError("network", "too_many_redirects")

    def json(self, url: str, *, headers=None, payload=None):
        body, _, _ = self.request(
            "POST" if payload is not None else "GET",
            url,
            headers=headers,
            payload=payload,
            max_bytes=16 * 1024 * 1024,
        )
        try:
            return json.loads(body)
        except (ValueError, UnicodeError) as exc:
            raise ServiceError("network", "invalid_json") from exc

    def text(self, url: str, *, headers=None, max_bytes=262144) -> str:
        body, _, _ = self.request("GET", url, headers=headers, max_bytes=max_bytes)
        if b"\0" in body:
            raise ServiceError("network", "binary_content")
        try:
            return body.decode("utf-8")
        except UnicodeError as exc:
            raise ServiceError("network", "unsupported_encoding") from exc


class BrowserSession:
    def __init__(self, context, destinations, failures, documents_only=False):
        self.context = context
        self.destinations, self.failures = destinations, failures
        self.documents_only = documents_only

    async def snapshot(self, url: str) -> dict:
        from playwright.async_api import TimeoutError as BrowserTimeout

        public_target(url)
        self.failures.pop(url, None)
        page = await self.context.new_page()
        try:
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            except Exception:
                if url in self.failures:
                    raise self.failures[url]
                raise
            if not self.documents_only:
                try:
                    await page.wait_for_load_state("networkidle", timeout=5000)
                except BrowserTimeout:
                    pass
            scripts = await page.locator(
                'script[type="application/json"][data-target="react-app.embeddedData"]'
            ).all_text_contents()
            payloads = []
            for script in scripts:
                try:
                    payloads.append(json.loads(script))
                except ValueError:
                    continue
            return {
                "url": self.destinations.get(page.url, page.url),
                "title": await page.title(),
                "text": await page.locator("body").inner_text(timeout=5000),
                "payloads": payloads,
                "links": await page.locator("a[href]").evaluate_all(
                    "elements => elements.slice(0, 3000).map(a => "
                    "({href: a.getAttribute('href'), text: a.textContent.trim()}))"
                ),
            }
        finally:
            await page.close()

    async def read(self, url: str) -> str:
        return (await self.snapshot(url))["text"]


class BrowserReader:
    def __init__(self, http: SafeHTTP):
        self.http = http

    def read(self, url: str) -> str:
        return self.run(lambda session: session.read(url))

    def snapshot(self, url: str) -> dict:
        return self.run(lambda session: session.snapshot(url), documents_only=True)

    def run(self, callback, *, documents_only=False):
        try:
            return asyncio.run(self._browse(callback, documents_only=documents_only))
        except ServiceError:
            raise
        except Exception as exc:
            raise ServiceError("browser", "render_failed") from exc

    async def _browse(self, callback, *, documents_only=False):
        from playwright.async_api import Error as BrowserError
        from playwright.async_api import async_playwright

        semaphore = asyncio.Semaphore(4)
        pending = set()
        counts = {}
        destinations, failures = {}, {}

        async def intercept(route):
            task = asyncio.current_task()
            pending.add(task)
            try:
                req = route.request
                page = req.frame.page
                count = counts.setdefault(page, [0, 0])
                if (
                    req.method != "GET"
                    or req.resource_type in {"image", "media", "font"}
                    or (documents_only and req.resource_type != "document")
                    or count[0] >= 48
                    or count[1] >= 16 * 1024 * 1024
                ):
                    await route.abort()
                    return
                count[0] += 1
                async with semaphore:
                    body, headers, final = await asyncio.to_thread(
                        self.http.request,
                        "GET",
                        req.url,
                        timeout=20 if documents_only else 8,
                        redirects=2,
                    )
                    destinations[req.url] = final
                    count[1] += len(body)
                    if count[1] > 16 * 1024 * 1024:
                        await route.abort()
                        return
                    await route.fulfill(
                        status=200,
                        content_type=headers.get("content-type", "text/plain"),
                        body=body,
                    )
            except (ServiceError, ValueError, BrowserError) as exc:
                if isinstance(exc, ServiceError) and route.request.resource_type == "document":
                    failures[route.request.url] = exc
                try:
                    await route.abort()
                except BrowserError:
                    pass
            finally:
                pending.discard(task)

        async def block_socket(ws):
            await ws.close()

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=[
                    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                    "--disable-background-networking",
                ],
            )
            context = await browser.new_context(
                service_workers="block",
                accept_downloads=False,
                java_script_enabled=not documents_only,
            )
            try:
                await context.route("**/*", intercept)
                await context.route_web_socket("**/*", block_socket)
                return await callback(
                    BrowserSession(context, destinations, failures, documents_only)
                )
            finally:
                await context.unroute_all(behavior="ignoreErrors")
                tasks = list(pending)
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                await context.close()
                await browser.close()
