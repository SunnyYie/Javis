import importlib.util
from types import SimpleNamespace

import pytest

from interview_question_bank.config import ROOT
from interview_question_bank.models import ServiceError

spec = importlib.util.spec_from_file_location(
    "import_iqb_sources", ROOT / "scripts" / "import_interview_sources.py",
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
detail_links = module.detail_links
markdown_paths = module.markdown_paths
readable = module.readable
spread_sample = module.spread_sample
decode_page = module.decode_page


def test_details_stay_in_supplied_directory():
    body = """<a href="/interview/qa/one">one</a>
    <a href="/interview/qa/one#answer">duplicate</a>
    <a href="https://elsewhere.test/interview/qa/two">external</a>
    <a href="/interview/qa/three?token=x">query</a>
    <a href="/interview/qa/%2e%2e/admin">encoded traversal</a>
    <a href="/interview/qa/">index</a><a href="/admin/">admin</a>"""
    assert detail_links(body, "https://example.test/interview/qa", "/interview/qa/") == [
        "https://example.test/interview/qa/one"
    ]


@pytest.mark.parametrize("limit", [1, 2, 3, 10, 60, 600])
def test_sampling_is_bounded_unique_and_stable(limit):
    values = [str(i) for i in range(573)]
    sample = spread_sample(values, limit)
    assert len(sample) == min(limit, len(values))
    assert len(set(sample)) == len(sample)
    assert sample == spread_sample(values, limit)
    if limit == 60:
        assert sample[:10] == values[:10]
        assert int(sample[-1]) > 550


def test_markdown_uses_only_linked_interview_documents():
    body = """[RAG](03-RAG技术.md)
    [foreign](https://evil.test/03-RAG技术.md)
    [outside](../../project-python/README.md)
    [traversal](../../../../../unsafe.md)
    [encoded](%2e%2e/%2e%2e/unsafe.md)
    [anchor](#heading)
    [image](../../comics/01.png)
    [duplicate](03-RAG技术.md)"""
    assert markdown_paths(body, "docs/01-面试八股文/README.md",
                          ("docs/01-面试八股文/",)) == [
        "docs/01-面试八股文/03-RAG技术.md", "docs/01-面试八股文/README.md"
    ]


def test_question_title_survives_extraction():
    answer = "An agent runs a controlled loop, uses tools and persists state. " * 10
    title, text = readable(
        f"<html><title>Agent</title><main><h1>What is an agent?</h1>"
        f"<article><p>{answer}</p></article></main></html>"
    )
    assert title == "What is an agent?"
    assert "What is an agent?" in text
    assert "persists state" in text


def test_challenge_and_empty_pages_not_evidence():
    with pytest.raises(ServiceError, match="access_challenge"):
        readable("<html><title>Just a moment...</title><p>verify you are human</p></html>")
    with pytest.raises(ServiceError, match="no_readable_body"):
        readable("<html><p>Please log in.</p></html>")


def test_malformed_html_normalization_is_not_binary_acceptance():
    text, count = decode_page(b"<html>Question\0Answer</html>", {"content-type": "text/html"})
    assert count == 1
    assert "\0" not in text
    assert "Question" in text and "Answer" in text
    for headers in ({}, {"content-type": "application/octet-stream"}):
        with pytest.raises(ServiceError, match="binary_content"):
            decode_page(b"\0binary", headers)
    with pytest.raises(ServiceError, match="unsupported_encoding"):
        decode_page(b"\xff", {"content-type": "text/html"})


def test_expansion_can_skip_existing_pages_without_fetching_them():
    entries = []
    importer = SimpleNamespace(
        skip_existing=True, store=SimpleNamespace(source=lambda _: {"status": "ok"}),
        note=lambda **kw: entries.append(kw),
    )
    module.Importer.web_page(importer, "agentalpha", "https://example.test/existing")
    assert entries == [{
        "group": "agentalpha", "url": "https://example.test/existing",
        "status": "already_stored",
    }]
