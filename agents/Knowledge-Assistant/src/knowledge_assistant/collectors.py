import copy
import json
import re
from collections import deque
from pathlib import PurePosixPath
from urllib.parse import parse_qs, quote, urlsplit

from .config import Settings
from .models import Bundle, Document, ServiceError
from .network import BrowserReader, SafeHTTP

REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
EXTENSIONS = {
    ".md",
    ".mdx",
    ".rst",
    ".txt",
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".go",
    ".rs",
    ".java",
    ".c",
    ".h",
    ".cpp",
    ".yaml",
    ".yml",
    ".toml",
}
EXCLUDED = {
    "node_modules",
    "vendor",
    ".git",
    "dist",
    "build",
    "__pycache__",
    ".venv",
    "site-packages",
    "target",
    "generated",
    "third_party",
    "third-party",
}


def repository(value: str) -> str | None:
    if REPO.fullmatch(value):
        return value.removesuffix(".git")
    p = urlsplit(value)
    if p.scheme == "https" and p.hostname == "github.com":
        parts = p.path.strip("/").split("/")
        if len(parts) == 2 and REPO.fullmatch("/".join(parts)):
            return "/".join(parts).removesuffix(".git")
    return None


def priority(path: str) -> tuple[int, int, str]:
    p = PurePosixPath(path.lower())
    if p.name.startswith("readme"):
        tier = 0 if len(p.parts) == 1 else 1
    elif any(x in p.parts for x in {"examples", "example", "tutorials"}):
        tier = 2
    elif p.suffix in {".md", ".mdx", ".rst", ".txt"}:
        tier = 1
    else:
        tier = 3
    config_penalty = 100 if p.suffix in {".yaml", ".yml", ".toml"} else 0
    return tier, config_penalty + len(p.parts), path


def balanced_entries(entries):
    """Reserve opportunities for examples and code in documentation-heavy repos."""
    groups = {i: deque() for i in range(4)}
    other = []
    for entry in sorted(entries, key=lambda e: priority(e["path"])):
        p = PurePosixPath(entry["path"])
        if (
            entry["type"] == "blob"
            and p.suffix.lower() in EXTENSIONS
            and not any(x.lower() in EXCLUDED for x in p.parts)
        ):
            groups[priority(entry["path"])[0]].append(entry)
        else:
            other.append(entry)
    result = list(groups[0])
    while any(groups[i] for i in (1, 2, 3)):
        for tier, slots in ((1, 3), (2, 1), (3, 1)):
            for _ in range(slots):
                if groups[tier]:
                    result.append(groups[tier].popleft())
    return result + other


class GitHubCollector:
    def __init__(self, settings: Settings, http: SafeHTTP):
        self.s, self.http = settings, http

    def api(self, path):
        headers = {"Accept": "application/vnd.github+json"}
        if token := self.s.github_token.get_secret_value():
            headers["Authorization"] = f"Bearer {token}"
        return self.http.json("https://api.github.com/" + path, headers=headers)

    def search(self, query: str) -> list[str]:
        data = self.api("search/repositories?q=" + quote(query[:200]) + "&per_page=3")
        return [r["html_url"] for r in data.get("items", [])]

    def collect(
        self,
        repo: str,
        *,
        role="unverified",
        paths: list[str] | None = None,
        file_limit: int | None = None,
    ) -> Bundle:
        info = self.api(f"repos/{repo}")
        if info.get("private"):
            raise ServiceError("github", "private_repository_not_supported")
        branch = quote(info["default_branch"], safe="")
        commit = self.api(f"repos/{repo}/commits/{branch}")["sha"]
        tree = self.api(f"repos/{repo}/git/trees/{commit}?recursive=1")
        if tree.get("truncated"):
            raise ServiceError("github", "truncated_tree")
        source = f"https://github.com/{repo}"
        license_id = (info.get("license") or {}).get("spdx_id", "unknown")
        if license_id == "NOASSERTION":
            license_id = "unknown"
        selected, skipped, documents = [], [], []
        size = 0
        entries = tree.get("tree", [])

        def order(entry):
            path = entry["path"]
            return (paths.index(path) if paths and path in paths else 100000, priority(path))

        ordered = sorted(entries, key=order) if paths else balanced_entries(entries)
        for entry in ordered:
            path = entry["path"]
            p = PurePosixPath(path)
            reason = ""
            if entry["type"] != "blob" or entry.get("mode") == "120000":
                reason = "not_regular_file"
            elif any(x.lower() in EXCLUDED for x in p.parts):
                reason = "excluded_directory"
            elif p.suffix.lower() not in EXTENSIONS:
                reason = "unsupported_type"
            elif any(x in p.name.lower() for x in {".min.", ".lock", "_pb2.", ".generated."}):
                reason = "generated"
            elif paths and path not in paths:
                reason = "outside_targeted_paths"
            elif entry.get("size", 0) > self.s.max_file_bytes:
                reason = "file_size_limit"
            elif len(selected) >= min(file_limit or self.s.max_files, self.s.max_files):
                reason = "file_count_limit"
            elif size + entry.get("size", 0) > self.s.max_repo_bytes:
                reason = "repository_size_limit"
            if reason:
                skipped.append({"path": path, "reason": reason})
                continue
            selected.append(path)
            size += entry.get("size", 0)
        for path in selected:
            try:
                content = self.http.text(
                    f"https://raw.githubusercontent.com/{repo}/{commit}/{quote(path)}",
                    max_bytes=self.s.max_file_bytes,
                )
            except ServiceError as exc:
                if exc.category in {"binary_content", "unsupported_encoding"}:
                    skipped.append({"path": path, "reason": exc.category})
                    continue
                raise  # Do not silently publish a snapshot with failed file reads.
            documents.append(
                Document(
                    source=source,
                    url=f"{source}/blob/{commit}/{quote(path)}",
                    text=content,
                    path=path,
                    repo=repo,
                    commit=commit,
                    license=license_id,
                    role=role,
                )
            )
        if not documents:
            raise ServiceError("github", "no_readable_documents")
        return Bundle(
            source,
            documents,
            {
                "selected": [d.path for d in documents],
                "skipped": skipped,
                "partial": True,
                "total_tree_entries": len(entries),
                "targeted": bool(paths),
            },
            repo,
            commit,
        )

    def targeted_paths(self, repo: str, query: str) -> list[str]:
        # GitHub code search needs authentication; failure remains explicit to the caller.
        data = self.api("search/code?q=" + quote(f"{query[:160]} repo:{repo}") + "&per_page=5")
        return [item["path"] for item in data.get("items", []) if item.get("path")]


class WebCollector:
    def __init__(self, settings: Settings, http: SafeHTTP):
        self.s, self.http = settings, http

    def search(self, query: str) -> list[str]:
        key = self.s.tavily_api_key.get_secret_value()
        if not key:
            raise ServiceError("search", "missing_api_key")
        if not self.s.search_endpoint.startswith("https://"):
            raise ServiceError("search", "https_required")
        data = self.http.json(
            self.s.search_endpoint,
            payload={"api_key": key, "query": query, "max_results": 5},
        )
        return [r["url"] for r in data.get("results", []) if r.get("url")]

    def collect(self, url: str) -> Bundle:
        import trafilatura

        html = self.http.text(url, max_bytes=2 * 1024 * 1024)
        text = trafilatura.extract(html, include_tables=True, include_comments=False) or ""
        if len(text.strip()) < 100 and self.s.browser_enabled:
            text = BrowserReader(self.http).read(url)
        if len(text.strip()) < 100:
            raise ServiceError("web", "no_readable_body")
        if len(text.encode()) > self.s.max_file_bytes:
            raise ServiceError("web", "body_size_limit")
        if any(
            s in text[:1000].lower()
            for s in (
                "verify you are human",
                "checking your browser",
                "enable javascript and cookies",
            )
        ):
            raise ServiceError("web", "access_challenge")
        document = Document(source=url, url=url, text=text, path="body", role="unverified")
        return Bundle(url, [document], {"selected": ["body"], "partial": False})


class Collectors:
    def __init__(self, settings: Settings, progress=lambda _: None):
        http = SafeHTTP()
        if settings.github_import_mode == "browser":
            from .github_browser import BrowserGitHubCollector

            self.github = BrowserGitHubCollector(settings, http, progress=progress)
        else:
            self.github = GitHubCollector(settings, http)
        self.web = WebCollector(settings, http)

    def collect(
        self, url: str, *, role="unverified", paths=None, file_limit=None, max_file_bytes=None
    ) -> Bundle:
        if repo := repository(url):
            targeted = parse_qs(urlsplit(url).query).get("paths", [])
            if targeted and not paths:
                try:
                    paths = json.loads(targeted[0])
                    if (
                        not isinstance(paths, list)
                        or not paths
                        or not all(isinstance(p, str) for p in paths)
                    ):
                        raise ValueError
                except ValueError as exc:
                    raise ServiceError("github", "invalid_targeted_paths") from exc
            collector = self.github
            if max_file_bytes is not None:
                if (
                    not isinstance(max_file_bytes, int)
                    or not 1 <= max_file_bytes <= self.s.max_repo_bytes
                ):
                    raise ServiceError("github", "invalid_file_size_limit")
                collector = copy.copy(collector)
                collector.s = self.s.model_copy(update={"max_file_bytes": max_file_bytes})
            bundle = collector.collect(repo, role=role, paths=paths, file_limit=file_limit)
            if targeted:
                bundle.source = url
                for doc in bundle.documents:
                    doc.source = url
            return bundle
        return self.web.collect(url)
