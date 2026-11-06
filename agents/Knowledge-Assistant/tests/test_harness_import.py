import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from knowledge_assistant import cli
from knowledge_assistant.models import Bundle, Document, ServiceError

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "import_harness_sources", ROOT / "scripts/import_harness_sources.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_targeted_import_preserves_previously_collected_files():
    seen, published = [], []
    entry = {"repo": "owner/repo", "role": "project", "paths": ["new.md"]}

    def collect(source, *, role, paths):
        seen.extend(paths)
        return Bundle(
            source,
            [Document(source, source, p, path=p, role=role) for p in paths],
            {"selected": paths},
            "owner/repo",
            "a" * 40,
        )

    store = SimpleNamespace(source=lambda _: {"coverage": json.dumps({"selected": ["old.md"]})})
    collectors = SimpleNamespace(
        github=SimpleNamespace(s=SimpleNamespace(max_files=60)), collect=collect
    )
    knowledge = SimpleNamespace(ingest=lambda b: published.append(b) or {"version": 2})
    result = MODULE.import_entry(entry, collectors, knowledge, store)
    assert seen == ["new.md", "old.md"]
    assert result["files"] == 2 and result["version"] == 2
    assert len(published) == 1


def test_incomplete_selected_source_never_publishes():
    source = "https://github.com/owner/repo"
    collectors = SimpleNamespace(
        github=SimpleNamespace(s=SimpleNamespace(max_files=60)),
        collect=lambda *args, **kwargs: Bundle(source, [], {}, "owner/repo"),
    )
    published = []
    with pytest.raises(ServiceError, match="incomplete_selected_files"):
        MODULE.import_entry(
            {"repo": "owner/repo", "role": "tutorial", "paths": ["missing.md"]},
            collectors,
            SimpleNamespace(ingest=published.append),
            SimpleNamespace(source=lambda _: None),
        )
    assert not published


def test_existing_and_new_files_share_budget():
    collectors = SimpleNamespace(github=SimpleNamespace(s=SimpleNamespace(max_files=1)))
    with pytest.raises(ValueError, match="file budget"):
        MODULE.import_entry(
            {"repo": "owner/repo", "role": "project", "paths": ["new.md"]},
            collectors,
            None,
            SimpleNamespace(source=lambda _: {"coverage": json.dumps({"selected": ["old.md"]})}),
        )


def test_harness_catalog_is_bounded_and_keeps_explicit_seed_projects():
    catalog = json.loads((ROOT / "harness-sources.json").read_text())
    seeds = {e["repo"]: e for e in json.loads((ROOT / "seeds.json").read_text())}
    assert len({e["repo"] for e in catalog}) == len(catalog)
    for entry in catalog:
        assert entry["topics"] and 0 < len(entry["paths"]) <= 60
        assert len(set(entry["paths"])) == len(entry["paths"])
    for repo in (
        "microsoft/agent-framework",
        "crewAIInc/crewAI",
        "shareAI-lab/learn-claude-code",
    ):
        assert repo in seeds and any(e["repo"] == repo for e in catalog)
    assert seeds["shareAI-lab/learn-claude-code"]["role"] == "tutorial"


def test_frozen_topic_checks_reference_selected_files_not_answers():
    catalog = {e["repo"]: e for e in json.loads((ROOT / "harness-sources.json").read_text())}
    cases = json.loads((ROOT / "eval/harness_checks.json").read_text())
    assert len(cases) == len({c["topic"] for c in cases}) == 19
    for case in cases:
        assert case["question"] and case["paths"]
        assert set(case["paths"]) <= set(catalog[case["repo"]]["paths"])
        assert case["topic"] in catalog[case["repo"]]["topics"]
        assert not {"answer", "reference", "reference_answer"} & case.keys()


def test_followup_checks_are_separate_and_reference_ingested_implementations():
    catalog = {e["repo"]: e for e in json.loads((ROOT / "harness-sources.json").read_text())}
    cases = json.loads((ROOT / "eval/harness_followup_checks.json").read_text())
    assert {c["topic"] for c in cases} == {"memory_selection_focused", "cost_enforcement"}
    for case in cases:
        assert case["question"] and case["paths"]
        assert set(case["paths"]) <= set(catalog[case["repo"]]["paths"])
        assert not {"answer", "reference", "reference_answer"} & case.keys()


@pytest.mark.parametrize("targeted", [True, False])
def test_sync_preserves_targeted_file_selection(env, monkeypatch, targeted):
    source = "https://github.com/owner/repo"
    bundle = Bundle(
        source,
        [Document(source, source, "Context document", path="guide.md", repo="owner/repo")],
        {"selected": ["guide.md"], "targeted": targeted, "max_file_bytes": 262144},
        "owner/repo",
        "a" * 40,
    )
    env.knowledge.ingest(bundle)
    calls = []
    monkeypatch.setattr(cli, "get_settings", lambda: env.s)
    monkeypatch.setattr(cli, "ingest_one", lambda *args, **kwargs: calls.append((args, kwargs)))
    result = CliRunner().invoke(cli.app, ["sync", "owner/repo"])
    assert result.exit_code == 0
    assert len(calls) == 1 and calls[0][0][2] == 262144
    assert calls[0][1]["paths"] == (["guide.md"] if targeted else None)
