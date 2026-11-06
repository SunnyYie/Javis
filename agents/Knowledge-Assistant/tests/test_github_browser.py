import asyncio

import pytest

from knowledge_assistant.collectors import Collectors, GitHubCollector
from knowledge_assistant.github_browser import BrowserGitHubCollector, safe_path
from knowledge_assistant.models import ServiceError

SHA = "a" * 40
SOURCE = "https://github.com/owner/repo"


class Pages:
    def __init__(self):
        self.files = {
            "README.md": "# Project\nA real source fixture, not live evidence.\n",
            "docs/guide.md": "# Guide\nRead before using.\n",
            "src/main.py": "def run():\n    return 'ok'\n",
        }
        self.commit = SHA
        self.mismatch = False
        self.truncated = False
        self.missing_lines = False
        self.private = False
        self.urls = []

    async def snapshot(self, url):
        self.urls.append(url)
        layout = {"repo": {"ownerLogin": "owner", "name": "repo", "private": self.private}}
        if "/blob/" in url:
            path = url.split("/blob/")[1].split("/", 1)[1]
            if path not in self.files:
                raise ServiceError("github.com", "not_found")
            view = {
                "refInfo": {"currentOid": "b" * 40 if self.mismatch else self.commit},
                "path": path,
                "blob": {"truncated": self.truncated, "headerInfo": {"mode": "file"}},
            }
            data = {
                "codeViewBlobLayoutRoute": view,
                "codeViewBlobLayoutRoute.StyledBlob": {}
                if self.missing_lines
                else {"rawLines": self.files[path].split("\n")},
            }
        else:
            directory = url.split("/tree/")[1].split("/", 1)[1] if "/tree/" in url else ""
            children = {}
            for path in self.files:
                if not path.startswith(directory + "/" if directory else ""):
                    continue
                rest = path[len(directory) + 1 :] if directory else path
                name = rest.split("/")[0]
                child = f"{directory}/{name}" if directory else name
                children[child] = "directory" if "/" in rest else "file"
            view = {
                "path": directory or "/",
                "refInfo": {"currentOid": self.commit},
                "tree": {
                    "items": [{"path": p, "contentType": t} for p, t in children.items()],
                    "totalCount": len(children),
                },
                "overview": {"sidebarAboutData": {"repo": {"license": None}}},
            }
            data = {"codeViewTreeRoute" if directory else "codeViewRepoRoute": view}
        data["codeViewLayoutRoute"] = layout
        return {"url": url, "payloads": [{"payload": data}]}


class Browser:
    def __init__(self):
        self.pages = Pages()

    def run(self, fn, *, documents_only):
        assert documents_only
        return asyncio.run(fn(self.pages))


def collector(env):
    browser = Browser()
    c = BrowserGitHubCollector(env.s, None, browser)
    return c, browser.pages


def test_browser_is_default_and_api_remains_explicit(env):
    assert isinstance(Collectors(env.s).github, BrowserGitHubCollector)
    env.s.github_import_mode = "api"
    assert type(Collectors(env.s).github) is GitHubCollector


def test_browser_pins_commit_and_keeps_exact_source_and_role(env):
    c, pages = collector(env)
    b = c.collect("owner/repo", role="third_party")
    assert b.repo == "owner/repo" and b.commit == SHA and b.complete
    assert b.coverage["transport"] == "browser" and b.coverage["partial"]
    assert b.coverage["expanded_directories"] == ["docs", "src"]
    assert {d.path: d.text for d in b.documents} == pages.files
    assert all(d.commit == SHA and d.role == "third_party" for d in b.documents)
    assert all(f"/blob/{SHA}/" in d.url and d.license == "unknown" for d in b.documents)
    assert all("api.github.com" not in u for u in pages.urls)
    assert all(SHA in u for u in pages.urls[1:])


def test_browser_file_and_directory_budgets_are_explicit(env):
    env.s.github_tree_pages, env.s.max_files = 0, 1
    c, pages = collector(env)
    b = c.collect("owner/repo")
    assert [d.path for d in b.documents] == ["README.md"]
    assert len(pages.urls) == 2
    assert {s["path"] for s in b.coverage["skipped"] if s["reason"] == "directory_page_limit"} == {
        "docs",
        "src",
    }


def test_targeted_paths_do_not_expand_unrelated_directories(env):
    c, pages = collector(env)
    b = c.collect("owner/repo", paths=["src/main.py"], file_limit=1)
    assert [d.path for d in b.documents] == ["src/main.py"]
    assert b.coverage["targeted"] and len(pages.urls) == 2


@pytest.mark.parametrize("inside", [True, False])
def test_compact_directory_paths_stay_within_requested_subtree(env, inside):
    c, pages = collector(env)
    original = pages.snapshot
    env.s.github_tree_pages = 1
    child = "docs/archive/deep" if inside else "other/archive"

    async def snapshot(url):
        result = await original(url)
        data = result["payloads"][0]["payload"]
        if "/tree/" in url:
            data["codeViewTreeRoute"]["tree"] = {
                "items": [{"path": child, "contentType": "directory", "hasSimplifiedPath": True}],
                "totalCount": 1,
            }
        return result

    pages.snapshot = snapshot
    if inside:
        bundle = c.collect("owner/repo")
        assert {"path": child, "reason": "directory_page_limit"} in bundle.coverage["skipped"]
    else:
        with pytest.raises(ServiceError, match="directory_scope_mismatch"):
            c.collect("owner/repo")


@pytest.mark.parametrize("path", ["../secret", "/absolute", "a/../b", "a//b", "a\\b"])
def test_invalid_source_paths_are_rejected(path):
    with pytest.raises(ServiceError, match="invalid_path"):
        safe_path(path)


@pytest.mark.parametrize(
    "attribute,category",
    [
        ("mismatch", "commit_mismatch"),
        ("missing_lines", "missing_source_lines"),
        ("private", "private_repository_not_supported"),
    ],
)
def test_browser_failures_do_not_publish_incomplete_sources(env, attribute, category):
    c, pages = collector(env)
    setattr(pages, attribute, True)
    with pytest.raises(ServiceError, match=category):
        c.collect("owner/repo")
    assert not env.store.sources()


def test_truncated_file_is_recorded_not_silently_ingested(env):
    c, pages = collector(env)
    pages.truncated = True
    with pytest.raises(ServiceError, match="truncated_file"):
        c.collect("owner/repo")


def test_size_limit_skips_whole_file_not_truncated_prefix(env):
    c, pages = collector(env)
    pages.files["README.md"] = "x" * 300
    env.s.max_file_bytes = 200
    b = c.collect("owner/repo")
    assert "README.md" not in [d.path for d in b.documents]
    assert {"path": "README.md", "reason": "file_size_limit"} in b.coverage["skipped"]


def test_repo_budget_is_checked_on_actual_utf8_bytes(env):
    c, pages = collector(env)
    pages.files = {"README.md": "中" * 30, "guide.md": "text" * 30}
    env.s.max_repo_bytes = 100
    b = c.collect("owner/repo")
    assert sum(len(d.text.encode()) for d in b.documents) <= 100
    assert {"path": "guide.md", "reason": "repository_size_limit"} in b.coverage["skipped"]


def test_browser_import_is_idempotent_and_handles_changes_and_deletion(env):
    c, pages = collector(env)
    first = c.collect("owner/repo")
    assert env.knowledge.ingest(first)["embedded"] > 0
    assert env.knowledge.ingest(c.collect("owner/repo"))["embedded"] == 0
    pages.files.pop("docs/guide.md")
    pages.files["src/main.py"] = "def run():\n    return 'changed'\n"
    pages.commit = "b" * 40
    updated = env.knowledge.ingest(c.collect("owner/repo"))
    assert updated["embedded"] > 0
    active = env.store.active_chunks()
    assert all(c.path != "docs/guide.md" and c.commit == "b" * 40 for c in active)


def test_browser_disabled_is_explicit_and_code_search_does_not_fake_no_results(env):
    c, _ = collector(env)
    env.s.browser_enabled = False
    with pytest.raises(ServiceError, match="browser_disabled"):
        c.collect("owner/repo")
    with pytest.raises(ServiceError, match="anonymous_code_search_unavailable"):
        c.targeted_paths("owner/repo", "query")


def test_new_catalog_has_thirteen_repos_and_scoped_large_readme_exception(env):
    import json

    rows = json.loads(env.s.seeds_file.read_text())
    assert len(rows) == len({r["repo"] for r in rows}) == 13
    awesome = next(r for r in rows if r["repo"] == "ai-boost/awesome-harness-engineering")
    assert awesome["role"] == "third_party" and awesome["max_file_bytes"] == 524288
    assert next(r for r in rows if r["repo"] == "infiniflow/ragflow")["role"] == "project"
    assert env.s.max_file_bytes == 262144
