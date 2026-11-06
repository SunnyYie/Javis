"""Import the five user-supplied public interview sources without executing their code."""

import argparse
import json
import re
import time
from pathlib import Path
from urllib.parse import quote, unquote, urldefrag, urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import trafilatura
from lxml import html
from markdown_it import MarkdownIt

from interview_question_bank.config import get_settings
from interview_question_bank.models import Bundle, Document, ServiceError
from interview_question_bank.network import SafeHTTP
from interview_question_bank.retrieval import Knowledge
from interview_question_bank.storage import Store

ALPHA = "https://agentalpha.top/interview/qa"
KAMA = "https://notes.kamacoder.com/interview/llm"
MIAN = "https://www.mianlingai.com/topics/ai-agent-interview-questions-2026/"
NAGE = "https://nageoffer.com/ai/interview/home/"
REPO = "https://github.com/bcefghj/ai-agent-interview-guide"
GROUPS = ("mianlingai", "kamacoder", "agentalpha", "nageoffer", "github")


def detail_links(markup: str, base: str, prefix: str, suffix="") -> list[str]:
    result = []
    for href in html.fromstring(markup).xpath("//a/@href"):
        url = urldefrag(urljoin(base, href))[0]
        parsed = urlsplit(url)
        if (
            parsed.scheme == "https"
            and parsed.netloc == urlsplit(base).netloc
            and parsed.path.startswith(prefix)
            and parsed.path.endswith(suffix)
            and parsed.path.rstrip("/") != prefix.rstrip("/")
            and not any(part in {".", ".."} for part in unquote(parsed.path).split("/"))
            and "\\" not in unquote(parsed.path)
            and not parsed.query
            and url not in result
        ):
            result.append(url)
    return result


def spread_sample(values: list[str], limit: int) -> list[str]:
    """Keep core opening questions and distribute remaining reads across the directory."""
    if len(values) <= limit:
        return values
    head = min(10, limit // 3)
    tail = values[head:]
    return values[:head] + [
        tail[i * len(tail) // (limit - head)] for i in range(limit - head)
    ]


def readable(markup: str) -> tuple[str, str]:
    tree = html.fromstring(markup)
    headings = tree.xpath("//main//h1 | //article//h1 | //h1")
    title = (
        headings[0].text_content().strip()
        if headings
        else " ".join(tree.xpath("//title/text()")).strip()
    )
    text = trafilatura.extract(
        markup, include_tables=True, include_comments=False, include_formatting=True
    ) or ""
    if any(term in (title + "\n" + text[:2000]).lower() for term in (
        "verify you are human", "checking your browser", "enable javascript and cookies",
        "just a moment...", "captcha", "人机验证", "访问过于频繁",
    )):
        raise ServiceError("web", "access_challenge")
    if len(text.strip()) < 100:
        raise ServiceError("web", "no_readable_body")
    if title and title not in text[:500]:
        text = f"# {title}\n\n{text}"
    return title, text


def markdown_paths(text: str, current: str, allowed: tuple[str, ...]) -> list[str]:
    paths = []
    base = f"https://raw.githubusercontent.com/bcefghj/ai-agent-interview-guide/REV/{current}"
    for token in MarkdownIt().parse(text):
        for child in token.children or []:
            if child.type != "link_open":
                continue
            target = urlsplit(urljoin(base, child.attrGet("href") or ""))
            prefix = "/bcefghj/ai-agent-interview-guide/REV/"
            if target.netloc != "raw.githubusercontent.com" or not target.path.startswith(prefix):
                continue
            path = unquote(target.path[len(prefix):])
            if (
                path.endswith(".md") and path.startswith(allowed) and path not in paths
                and not any(part in {".", ".."} for part in path.split("/"))
                and "\\" not in path
            ):
                paths.append(path)
    return paths


def decode_page(body: bytes, headers: dict) -> tuple[str, int]:
    content_type = headers.get("content-type", "").lower().split(";")[0].strip()
    nul_count = body.count(b"\0")
    if nul_count and content_type != "text/html":
        raise ServiceError("network", "binary_content")
    try:
        # HTML parsers replace NUL characters; do not silently accept binary MIME bodies.
        return body.decode("utf-8").replace("\0", "\ufffd"), nul_count
    except UnicodeError as exc:
        raise ServiceError("network", "unsupported_encoding") from exc


class Importer:
    def __init__(self, report_path: Path, skip_existing=False):
        self.settings = get_settings()
        self.store = Store(self.settings.data_dir)
        self.knowledge = Knowledge(self.settings, self.store)
        self.http = SafeHTTP()
        self.report_path = report_path
        self.robots = {}
        self.skip_existing = skip_existing
        self.report = {
            "started_at": time.time(),
            "data_dir": str(self.settings.data_dir),
            "model": self.settings.llm_model,
            "llm_calls": 0,
            "initial_version": self.store.version,
            "groups": {},
            "attempts": [],
        }

    def save(self):
        self.report["knowledge_version"] = self.store.version
        self.report_path.parent.mkdir(parents=True, exist_ok=True)
        self.report_path.write_text(
            json.dumps(self.report, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def note(self, **entry):
        self.report["attempts"].append(dict(at=time.time(), **entry))
        print(json.dumps(entry, ensure_ascii=False), flush=True)
        self.save()

    def fetch(self, url: str) -> str:
        origin = f"{urlsplit(url).scheme}://{urlsplit(url).netloc}"
        if origin not in self.robots:
            robot = RobotFileParser()
            try:
                body = self.http.text(origin + "/robots.txt", max_bytes=262144)
                if body.lstrip().lower().startswith(("<!doctype", "<html")):
                    self.note(url=origin + "/robots.txt", status="not_a_robots_file")
                    robot = None
                else:
                    robot.parse(body.splitlines())
            except ServiceError as exc:
                if exc.category != "not_found":
                    raise
                robot = None
            self.robots[origin] = robot
        robot = self.robots[origin]
        if robot and not robot.can_fetch("Interview-Question-Bank", url):
            raise ServiceError("robots", "disallowed")
        time.sleep(0.5)
        body, headers, _ = self.http.request("GET", url, max_bytes=2 * 1024 * 1024)
        text, nul_count = decode_page(body, headers)
        if nul_count:
            self.note(url=url, status="malformed_html_nul_replaced", count=nul_count)
        return text

    def web_page(self, group: str, url: str, *, markup=None, question_only=False):
        try:
            if self.skip_existing and self.store.source(url):
                self.note(group=group, url=url, status="already_stored")
                return
            body = markup if markup is not None else self.fetch(url)
            title, text = readable(body)
            if len(text.encode()) > self.settings.max_file_bytes:
                raise ServiceError("web", "body_size_limit")
            bundle = Bundle(
                url,
                [Document(source=url, url=url, text=text, path="body")],
                {
                    "selected": ["body"], "partial": False,
                    "import_group": group, "title": title,
                    "question_only": question_only,
                    "company_claims": "source_claims_not_independently_verified",
                },
            )
            outcome = self.knowledge.ingest(bundle)
            self.note(group=group, url=url, status="stored", documents=1,
                      text_bytes=len(text.encode()), **outcome)
        except ServiceError as exc:
            self.store.event(url, exc.category)
            self.note(group=group, url=url, status="failed", error=str(exc))

    def directory(self, group: str, url: str, prefix: str, suffix: str, limit: int):
        markup = self.fetch(url)
        links = detail_links(markup, url, prefix, suffix)
        selected = spread_sample(links, limit)
        if not selected:
            raise ServiceError("directory", "no_question_links")
        self.report["groups"][group] = {
            "entry": url, "discovered": len(links), "selected": selected,
            "not_selected": [link for link in links if link not in selected],
            "partial": len(selected) < len(links),
        }
        self.save()
        for link in selected:
            self.web_page(group, link)

    def github(self):
        # API quota exhaustion does not prevent reading the normal public repository pages.
        try:
            self.http.json("https://api.github.com/repos/bcefghj/ai-agent-interview-guide")
            self.note(group="github", url=REPO, status="api_available")
        except ServiceError as exc:
            self.note(group="github", url=REPO, status="api_failed", error=str(exc))
        tree = html.fromstring(self.fetch(REPO))
        nodes = tree.xpath('//script[@data-target="react-app.embeddedData"]/text()')
        if not nodes:
            raise ServiceError("github", "public_metadata_missing")
        route = json.loads(nodes[0])["payload"]["codeViewRepoRoute"]
        commit = route["refInfo"]["currentOid"]
        if not re.fullmatch("[0-9a-f]{40}", commit):
            raise ServiceError("github", "invalid_commit")
        paths = ["README.md"]
        allowed = ("docs/", "project-python/", "project-java/", "project-go/")
        documents = []
        raw_base = f"https://raw.githubusercontent.com/bcefghj/ai-agent-interview-guide/{commit}/"
        license_text = self.fetch(raw_base + "LICENSE")
        license_id = "MIT" if license_text.startswith("MIT License") else "unknown"
        total = 0
        for path in paths:
            text = self.fetch(raw_base + quote(path))
            total += len(text.encode())
            if len(text.encode()) > self.settings.max_file_bytes or total > self.settings.max_repo_bytes:
                raise ServiceError("github", "body_size_limit")
            documents.append(Document(
                source=REPO, url=f"{REPO}/blob/{commit}/{quote(path)}", text=text, path=path,
                repo="bcefghj/ai-agent-interview-guide", commit=commit, license=license_id,
            ))
            for linked in markdown_paths(text, path, allowed):
                if linked not in paths:
                    if len(paths) >= self.settings.max_files:
                        raise ServiceError("github", "file_count_limit")
                    paths.append(linked)
        documents.append(Document(
            source=REPO, url=f"{REPO}/blob/{commit}/LICENSE", text=license_text, path="LICENSE",
            repo="bcefghj/ai-agent-interview-guide", commit=commit, license=license_id,
        ))
        coverage = {
            "selected": [d.path for d in documents], "partial": True,
            "acquisition": "public_html_commit_metadata_and_pinned_raw_markdown",
            "scope": "linked_technical_markdown_only_no_repository_code_execution",
            "license_notice": (
                "LICENSE states MIT; repository README additionally restricts learning "
                "materials to non-commercial reference. No commercial rights inferred."
            ),
        }
        bundle = Bundle(REPO, documents, coverage, "bcefghj/ai-agent-interview-guide", commit)
        self.report["groups"]["github"] = dict(entry=REPO, commit=commit, **coverage)
        result = self.knowledge.ingest(bundle)
        self.note(group="github", url=REPO, status="stored", documents=len(documents), **result)

    def run(self, groups: list[str], alpha_limit: int):
        for group in groups:
            try:
                if group == "mianlingai":
                    self.report["groups"][group] = {"entry": MIAN, "scope": "supplied_article"}
                    self.web_page(group, MIAN)
                elif group == "kamacoder":
                    self.directory(group, KAMA, "/interview/llm/", ".html", 60)
                elif group == "agentalpha":
                    self.directory(group, ALPHA, "/interview/qa/", "", alpha_limit)
                elif group == "nageoffer":
                    self.report["groups"][group] = {
                        "entry": NAGE, "scope": "public_top50_question_list",
                        "question_only": True, "partial": True,
                    }
                    self.web_page(
                        group, urljoin(NAGE, "../top50/"), question_only=True
                    )
                elif group == "github":
                    self.github()
            except (ServiceError, KeyError, ValueError) as exc:
                category = str(exc) if isinstance(exc, ServiceError) else "public_metadata_invalid"
                self.note(group=group, status="failed", error=category)
        self.report["finished_at"] = time.time()
        self.report["sources"] = len(self.store.sources())
        self.report["active_chunks"] = len(self.store.active_chunks())
        self.save()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", nargs="+", choices=GROUPS, default=list(GROUPS))
    parser.add_argument("--alpha-limit", type=int, default=60)
    parser.add_argument("--skip-existing", action="store_true")
    parser.add_argument("--report", type=Path, default=Path("data/imports/provided-sources.json"))
    args = parser.parse_args()
    if not 1 <= args.alpha_limit <= 600:
        parser.error("--alpha-limit must be between 1 and 600")
    if args.report.exists():
        parser.error("Report already exists; choose a new --report path to preserve evidence.")
    Importer(args.report, skip_existing=args.skip_existing).run(args.only, args.alpha_limit)


if __name__ == "__main__":
    main()
