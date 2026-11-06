"""Read public repository metadata and source lines from GitHub's browser pages."""

import asyncio
import re
from pathlib import PurePosixPath
from urllib.parse import quote, urlsplit

from .collectors import (
    EXCLUDED,
    EXTENSIONS,
    GitHubCollector,
    balanced_entries,
    priority,
    repository,
)
from .models import Bundle, Document, ServiceError
from .network import BrowserReader

SHA = re.compile(r"^[0-9a-f]{40}$")


def safe_path(path):
    if (
        not isinstance(path, str)
        or not path
        or path.startswith("/")
        or "\\" in path
        or "\0" in path
        or any(part in {"", ".", ".."} for part in path.split("/"))
    ):
        raise ServiceError("github_browser", "invalid_path")
    return path


def payload(snapshot):
    if urlsplit(snapshot["url"]).hostname != "github.com":
        raise ServiceError("github_browser", "unexpected_redirect")
    for item in snapshot.get("payloads", []):
        data = item.get("payload", {})
        if "codeViewLayoutRoute" in data:
            if data["codeViewLayoutRoute"].get("repo", {}).get("private"):
                raise ServiceError("github_browser", "private_repository_not_supported")
            return data
    raise ServiceError("github_browser", "repository_page_unavailable")


def route(data):
    for key in ("codeViewRepoRoute", "codeViewTreeRoute", "codeViewBlobLayoutRoute"):
        if key in data:
            return data[key]
    raise ServiceError("github_browser", "unsupported_page_structure")


def checked_route(snapshot, commit=None, path=None):
    data = payload(snapshot)
    view = route(data)
    oid = view.get("refInfo", {}).get("currentOid", "")
    if not SHA.fullmatch(oid) or (commit and oid != commit):
        raise ServiceError("github_browser", "commit_mismatch")
    if path is not None and view.get("path", "").strip("/") != path:
        raise ServiceError("github_browser", "path_mismatch")
    return data, view, oid


def entries(view):
    tree = view.get("tree", {})
    items = tree.get("items")
    if not isinstance(items, list):
        raise ServiceError("github_browser", "missing_directory")
    # Never claim a complete directory when GitHub only returned a subset.
    if int(tree.get("totalCount", len(items))) != len(items):
        raise ServiceError("github_browser", "truncated_directory")
    result = []
    for item in items:
        kind = item.get("contentType")
        result.append(
            {
                "path": safe_path(item["path"]),
                "type": "tree" if kind == "directory" else "blob",
                "mode": "120000" if kind not in {"directory", "file"} else "100644",
            }
        )
    return result


def source_lines(data):
    view = data["codeViewBlobLayoutRoute"]
    blob = view.get("blob", {})
    info = blob.get("headerInfo", {})
    if info.get("isGitLfs") or info.get("mode") not in {None, "file"}:
        raise ServiceError("github_browser", "not_regular_file")
    if blob.get("truncated") or blob.get("large") or not blob.get("viewable", True):
        raise ServiceError("github_browser", "truncated_file")
    lines = data.get("codeViewBlobLayoutRoute.StyledBlob", {}).get("rawLines")
    if not isinstance(lines, list) or not all(isinstance(line, str) for line in lines):
        raise ServiceError("github_browser", "missing_source_lines")
    text = "\n".join(lines)
    if "\0" in text:
        raise ServiceError("github_browser", "binary_content")
    return text


def skip_reason(entry):
    p = PurePosixPath(entry["path"])
    if entry.get("mode") == "120000":
        return "not_regular_file"
    if any(part.lower() in EXCLUDED or part.startswith(".") for part in p.parts[:-1]):
        return "excluded_directory"
    if p.suffix.lower() not in EXTENSIONS:
        return "unsupported_type"
    if any(token in p.name.lower() for token in (".min.", ".lock", "_pb2.", ".generated.")):
        return "generated"
    return ""


class BrowserGitHubCollector(GitHubCollector):
    def __init__(self, settings, http, browser=None, progress=lambda _: None):
        super().__init__(settings, http)
        self.browser = browser or BrowserReader(http)
        self.progress = progress

    def collect(self, repo, *, role="unverified", paths=None, file_limit=None):
        if not self.s.browser_enabled:
            raise ServiceError("github_browser", "browser_disabled")
        if not repository(repo):
            raise ServiceError("github_browser", "invalid_repository")
        if paths:
            paths = list(dict.fromkeys(safe_path(p) for p in paths))
        return self.browser.run(
            lambda session: self._collect(session, repo, role, paths, file_limit),
            documents_only=True,
        )

    async def snapshot(self, session, url):
        self.progress(url)
        for attempt in range(2):
            try:
                return await session.snapshot(url)
            except ServiceError as exc:
                if exc.category != "connection_failed" or attempt:
                    raise
                await asyncio.sleep(1)

    async def _collect(self, session, repo, role, paths, file_limit):
        root = await self.snapshot(session, f"https://github.com/{repo}")
        data, view, commit = checked_route(root, path="")
        actual = data["codeViewLayoutRoute"].get("repo", {})
        canonical = f"{actual.get('ownerLogin', '')}/{actual.get('name', '')}"
        if not repository(canonical):
            raise ServiceError("github_browser", "missing_repository_identity")
        source = f"https://github.com/{canonical}"
        about = view.get("overview", {}).get("sidebarAboutData", {})
        license_id = (about.get("repo", {}).get("license") or {}).get("spdxId") or "unknown"
        if license_id == "NOASSERTION":
            license_id = "unknown"
        listing = {e["path"]: e for e in entries(view)}
        skipped, expanded = [], []
        limit = min(file_limit or self.s.max_files, self.s.max_files)
        if not paths:
            queue = [e for e in listing.values() if e["type"] == "tree"]
            while queue and limit > 1 and len(expanded) < self.s.github_tree_pages:
                queue.sort(
                    key=lambda e: (
                        len(PurePosixPath(e["path"]).parts),
                        0
                        if e["path"].split("/")[0].lower()
                        in {"docs", "doc", "examples", "src", "lib", "libs"}
                        else 1,
                        priority(e["path"]),
                    )
                )
                directory = queue.pop(0)
                path = directory["path"]
                if any(
                    part.lower() in EXCLUDED or part.startswith(".")
                    for part in PurePosixPath(path).parts
                ):
                    skipped.append({"path": path, "reason": "excluded_directory"})
                    continue
                page = await self.snapshot(
                    session, f"{source}/tree/{commit}/{quote(path, safe='/')}"
                )
                _, subtree, _ = checked_route(page, commit, path)
                children = entries(subtree)
                # GitHub folds chains such as docs/archive/remote-r3 into one directory item.
                if any(
                    PurePosixPath(path) not in PurePosixPath(e["path"]).parents for e in children
                ):
                    raise ServiceError("github_browser", "directory_scope_mismatch")
                expanded.append(path)
                listing.update((e["path"], e) for e in children)
                queue.extend(e for e in children if e["type"] == "tree")
            skipped.extend({"path": e["path"], "reason": "directory_page_limit"} for e in queue)
        ordered = (
            [{"path": p, "type": "blob", "mode": "100644"} for p in paths]
            if paths
            else balanced_entries(list(listing.values()))
        )
        documents, attempted, total = [], [], 0
        for entry in ordered:
            path = entry["path"]
            if entry["type"] == "tree":
                continue
            reason = skip_reason(entry)
            if not reason and len(attempted) >= limit:
                reason = "file_count_limit"
            if reason:
                skipped.append({"path": path, "reason": reason})
                continue
            attempted.append(path)
            page = await self.snapshot(session, f"{source}/blob/{commit}/{quote(path, safe='/')}")
            blob_data, _, _ = checked_route(page, commit, path)
            try:
                text = source_lines(blob_data)
            except ServiceError as exc:
                if exc.category in {"not_regular_file", "binary_content"}:
                    skipped.append({"path": path, "reason": exc.category})
                    continue
                raise
            size = len(text.encode())
            if size > self.s.max_file_bytes:
                skipped.append({"path": path, "reason": "file_size_limit"})
                continue
            if total + size > self.s.max_repo_bytes:
                skipped.append({"path": path, "reason": "repository_size_limit"})
                continue
            total += size
            documents.append(
                Document(
                    source=source,
                    url=f"{source}/blob/{commit}/{quote(path, safe='/')}",
                    text=text,
                    path=path,
                    repo=canonical,
                    commit=commit,
                    license=license_id,
                    role=role,
                )
            )
        if not documents:
            raise ServiceError("github_browser", "no_readable_documents")
        return Bundle(
            source,
            documents,
            {
                "transport": "browser",
                "selected": [d.path for d in documents],
                "skipped": skipped,
                "partial": True,
                "targeted": bool(paths),
                "expanded_directories": expanded,
                "file_pages_read": attempted,
                "discovered_entries": len(listing),
                "max_file_bytes": self.s.max_file_bytes,
            },
            canonical,
            commit,
        )

    def targeted_paths(self, repo, query):
        # Explicit supplemental paths remain supported; anonymous code search is login-gated.
        raise ServiceError("github_browser", "anonymous_code_search_unavailable")
