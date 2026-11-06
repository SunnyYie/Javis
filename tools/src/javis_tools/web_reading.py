"""Search candidates are not evidence: read bounded public article bodies first."""

import hashlib
import time
from urllib.parse import urlsplit

from knowledge_assistant.collectors import WebCollector
from knowledge_assistant.models import ServiceError
from knowledge_assistant.network import SafeHTTP


def article_url(url):
    try:
        parsed = urlsplit(url)
        return bool(
            parsed.scheme == "https" and parsed.hostname
            and not parsed.username and not parsed.password and not parsed.query
            and parsed.hostname not in {"github.com", "raw.githubusercontent.com"}
            and len(parsed.path.strip("/").split("/")) >= 1
            and parsed.path.strip("/").casefold() not in {
                "", "blog", "blogs", "docs", "articles", "search", "tags", "category",
            }
        )
    except ValueError:
        return False


def search_articles(settings, query, *, http=None):
    import trafilatura
    from trafilatura.metadata import extract_metadata

    http = http or SafeHTTP()
    web = WebCollector(settings, http)
    warnings, articles = [], []
    try:
        urls = web.search(query[:350])
    except ServiceError:
        return {"articles": [], "warnings": ["article_search_unavailable"]}
    seen = set()
    for url in urls[:5]:
        if len(articles) >= 5:
            break
        if not article_url(url) or url in seen:
            continue
        seen.add(url)
        try:
            body, headers, final_url = http.request(
                "GET", url, max_bytes=1024 * 1024, redirects=3, timeout=12,
            )
            if not article_url(final_url) or final_url in {a["url"] for a in articles}:
                continue
            if "html" not in headers.get("content-type", "").casefold():
                continue
            text = trafilatura.extract(body, include_tables=False, include_comments=False) or ""
            metadata = extract_metadata(body, default_url=final_url)
            title = metadata.title if metadata else ""
            if (
                not title or len(text.strip()) < 200
                or any(s in text[:1000].casefold() for s in (
                    "verify you are human", "checking your browser", "access denied",
                ))
            ):
                continue
            articles.append({
                "id": "web:" + hashlib.sha256(final_url.encode()).hexdigest()[:16],
                "url": final_url, "title": title[:240], "text": text[:12000],
                "fetched_at": time.time(),
            })
        except ServiceError:
            warnings.append("article_read_failed")
    return {"articles": articles, "warnings": sorted(set(warnings))}
