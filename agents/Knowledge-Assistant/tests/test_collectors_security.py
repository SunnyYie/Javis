import json
import socket
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest

from knowledge_assistant.collectors import (
    Collectors,
    GitHubCollector,
    WebCollector,
    balanced_entries,
    priority,
    repository,
)
from knowledge_assistant.models import ServiceError
from knowledge_assistant.network import SafeHTTP, public_target


class GitHTTP:
    def __init__(self):
        self.files = [
            {"path": "README.md", "type": "blob", "size": 150},
            {"path": "docs/guide.md", "type": "blob", "size": 120},
            {"path": "src/main.py", "type": "blob", "size": 50},
            {"path": "node_modules/pkg/index.js", "type": "blob", "size": 50},
            {"path": "dist/generated.js", "type": "blob", "size": 50},
            {"path": "movie.mp4", "type": "blob", "size": 1000},
            {"path": "oversized.md", "type": "blob", "size": 300000},
            {"path": "link.md", "type": "blob", "mode": "120000", "size": 10},
        ]
        self.truncated = False
        self.failed = False

    def json(self, url, **kwargs):
        if "/git/trees/" in url:
            return {"tree": self.files, "truncated": self.truncated}
        if "/commits/" in url:
            return {"sha": "123abc"}
        return {"default_branch": "main", "license": {"spdx_id": "MIT"}}

    def text(self, url, **kwargs):
        if self.failed:
            raise ServiceError("github", "rate_limited")
        return "# Project\n\nReal documentation\n"


@pytest.mark.parametrize(
    "value,expected",
    [
        ("owner/repo", "owner/repo"),
        ("https://github.com/owner/repo.git", "owner/repo"),
        ("https://github.com/owner/repo/", "owner/repo"),
        ("https://github.com/owner/repo/blob/main/README.md", None),
        ("https://github.com.evil.test/owner/repo", None),
    ],
)
def test_repository_normalization(value, expected):
    assert repository(value) == expected


def test_collection_limits_exclusions_and_pinned_provenance(env):
    env.s.max_files = 2
    b = GitHubCollector(env.s, GitHTTP()).collect("owner/repo", role="third_party")
    assert b.commit == "123abc" and b.complete
    assert [d.path for d in b.documents] == ["README.md", "docs/guide.md"]
    assert all("/123abc/" in d.url and d.role == "third_party" for d in b.documents)
    skipped = {r["path"]: r["reason"] for r in b.coverage["skipped"]}
    assert skipped["src/main.py"] == "file_count_limit"
    assert skipped["oversized.md"] == "file_size_limit"
    assert skipped["link.md"] == "not_regular_file"
    assert skipped["dist/generated.js"] == "excluded_directory"


def test_targeted_source_prioritizes_requested_paths(env):
    b = GitHubCollector(env.s, GitHTTP()).collect(
        "owner/repo",
        paths=["src/main.py", "README.md"],
        file_limit=1,
    )
    assert [d.path for d in b.documents] == ["src/main.py"]


@pytest.mark.parametrize("failure", ["truncated", "failed"])
def test_incomplete_github_reads_fail_not_partial_publish(env, failure):
    http = GitHTTP()
    setattr(http, failure, True)
    with pytest.raises(ServiceError):
        GitHubCollector(env.s, http).collect("owner/repo")


def test_readme_precedes_source():
    assert priority("README.md") < priority("src/main.py")


def test_large_documentation_tree_still_includes_examples_and_source():
    entries = [{"path": f"docs/{i}.md", "type": "blob", "size": 100} for i in range(100)] + [
        {"path": "examples/main.py", "type": "blob", "size": 100},
        {"path": "src/main.py", "type": "blob", "size": 100},
    ]
    first = [e["path"] for e in balanced_entries(entries)[:10]]
    assert "src/main.py" in first and "examples/main.py" in first


def test_supplemental_paths_are_structured_and_do_not_replace_primary_source(env):
    collectors = Collectors(env.s)
    collectors.github = GitHubCollector(env.s, GitHTTP())
    url = "https://github.com/owner/repo?" + urlencode({"paths": json.dumps(["src/main.py"])})
    result = collectors.collect(url)
    assert result.source == url
    assert result.documents[0].source == url
    assert [d.path for d in result.documents] == ["src/main.py"]


def test_private_repo_is_not_sent_to_remote_model(env):
    http = SimpleNamespace(json=lambda *a, **k: {"private": True})
    with pytest.raises(ServiceError, match="private_repository_not_supported"):
        GitHubCollector(env.s, http).collect("owner/private")


def test_runtime_code_precedes_root_configuration():
    assert priority("src/agent/core.py") < priority("pyproject.toml")


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://user:password@example.com",
        "ftp://example.com/data",
        "http://example.com:22",
    ],
)
def test_unsafe_protocol_port_and_credentials(url):
    with pytest.raises(ServiceError):
        public_target(url)


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",
        "10.0.0.1",
        "192.168.0.1",
        "169.254.169.254",
        "::1",
        "fc00::1",
    ],
)
def test_private_dns_targets_rejected(monkeypatch, ip):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 80)),
        ],
    )
    with pytest.raises(ServiceError, match="private_address_blocked"):
        public_target("http://attacker.example/")


def test_public_target_is_resolved_once_and_pinned(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443)),
        ],
    )
    assert public_target("https://example.com") == ("example.com", 443, "8.8.8.8")


def test_redirect_is_revalidated_before_connect(monkeypatch):
    import knowledge_assistant.network as net

    calls = []

    def target(url):
        calls.append(url)
        if "localhost" in url:
            raise ServiceError("network", "private_address_blocked")
        return "public.example", 80, "8.8.8.8"

    class Response:
        status = 302

        def getheaders(self):
            return [("Location", "http://localhost/private")]

    class Connection:
        def __init__(self, *args, **kwargs):
            pass

        def request(self, *args, **kwargs):
            pass

        def getresponse(self):
            return Response()

        def close(self):
            pass

    monkeypatch.setattr(net, "public_target", target)
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: object())
    monkeypatch.setattr(net.http.client, "HTTPConnection", Connection)
    with pytest.raises(ServiceError, match="private_address"):
        SafeHTTP().request("GET", "http://public.example")
    assert len(calls) == 2


def test_search_snippet_never_accepted_as_body(env):
    env.s.browser_enabled = False
    http = SimpleNamespace(text=lambda *a, **k: "<html><body>snippet</body></html>")
    with pytest.raises(ServiceError, match="no_readable_body"):
        WebCollector(env.s, http).collect("https://example.com")


def test_missing_search_key_explicit(env):
    with pytest.raises(ServiceError, match="missing_api_key"):
        WebCollector(env.s, GitHTTP()).search("agents")
