import json
from types import SimpleNamespace

import pytest
from conftest import FakeIndex, bundle
from markdown_it import MarkdownIt

from interview_question_bank.bank import (
    Candidate,
    QuestionBank,
    extract_candidates,
    load_plan,
    nested_answer,
    title_key,
)
from interview_question_bank.models import (
    Draft,
    InterviewQuestion,
    InterviewRequest,
    ServiceError,
    Verification,
)


def setup_bank(env):
    text = (
        "# 什么是 LangGraph？\n\n"
        "LangGraph uses explicit state to execute a durable agent workflow. "
        "A checkpoint stores progress so execution can resume after interruption.\n\n"
        "## 如何恢复 LangGraph 状态？\n\n"
        "Restore the checkpoint and continue from the saved execution state. "
        "Do not repeat external effects without checking their idempotency.\n"
    )
    env.knowledge.ingest(bundle(text=text))
    index = FakeIndex()
    bank = QuestionBank(env.s, store=env.store, embeddings=env.embeddings, index=index)
    plan = {
        "version": "test-v1", "semantic_duplicate_threshold": .93,
        "topics": [{"id": "graph", "name": "编排", "hot": True, "quota": 2,
                    "keywords": ["LangGraph"], "facets": ["恢复"]}],
    }
    chunks = env.store.active_chunks()
    candidates = [
        Candidate(
            InterviewQuestion(
                title=title, difficulty="基础", knowledge_points=["状态"],
                answer=text.split("\n\n")[1], key_points=["Source text"],
                origin="来源原题", evidence_ids=[c.id for c in chunks],
                source_question_quote=title,
            ), chunks, plan["topics"][0], "doc", "source_exact",
        ) for title in ("什么是 LangGraph？", "如何恢复 LangGraph 状态？")
    ]
    return bank, index, plan, candidates


def test_curriculum_total_hot_weight_and_largest_remainder():
    plan = load_plan()
    assert sum(t["quota"] for t in plan["topics"]) == 1000
    assert sum(t["quota"] for t in plan["topics"] if t["hot"]) == 765
    assert len(plan["topics"]) == 18
    assert next(t["quota"] for t in plan["topics"] if t["id"] == "harness") == 100
    assert sum(t["quota"] for t in load_plan(target=23)["topics"]) == 23
    assert title_key("请解释ＭＣＰ 是什么？") == title_key("MCP 是什么?")


def test_markdown_answer_headings_remain_inside_question_and_code_is_unchanged():
    rendered = nested_answer("# Answer\n\n## Detail\n\n```python\n# code comment\n```\n")
    assert rendered.startswith("### Answer\n\n#### Detail\n")
    assert "```python\n# code comment\n```" in rendered


def test_original_extraction_keeps_full_answers_and_bound_source_ids(env):
    bank, _, plan, _ = setup_bank(env)
    candidates = extract_candidates(env.store, plan)
    assert len(candidates) == 2
    first = next(c for c in candidates if "什么" in c.question.title)
    assert "checkpoint" in first.question.answer
    assert first.question.origin == "来源原题" and first.review == "source_exact"
    assert set(first.question.evidence_ids) <= {c.id for c in env.store.active_chunks()}
    assert any(first.question.source_question_quote in c.text for c in first.chunks)
    assert bank.stats()["total"] == 0


def test_publish_isolated_vectors_and_exact_semantic_dedup_with_export(env, tmp_path):
    bank, index, plan, candidates = setup_bank(env)
    raw_ids = set(env.index.rows)
    assert bank.add(candidates, plan, vectors=[[1, 0, 0], [0, 1, 0]]) == 2
    assert len(index.rows) == 2 and set(env.index.rows) == raw_ids
    assert bank.add(candidates, plan, vectors=[[1, 0, 0], [0, 1, 0]]) == 0
    assert bank.stats()["total"] == bank.stats()["hot"] == 2
    assert bank.stats()["citations_active"] == 2
    assert env.store.db.execute("SELECT COUNT(*) FROM question_records").fetchone()[0] == 2
    assert bank.export(tmp_path / "questions.jsonl") == 2
    rows = [json.loads(line) for line in (tmp_path / "questions.jsonl").read_text().splitlines()]
    assert all(r["citations"] and r["origin"] == "来源原题" for r in rows)


def test_semantic_duplicate_is_rejected_even_with_different_title(env):
    bank, _, plan, candidates = setup_bank(env)
    assert bank.add(candidates, plan, vectors=[[1, 0, 0], [1, 0, 0]]) == 1
    assert bank.rejected["semantic_duplicate"] == 1


def test_markdown_multiline_key_points_do_not_become_extra_question_headings(env, tmp_path):
    bank, _, plan, candidates = setup_bank(env)
    candidates[0].question.key_points = ["## Summary\n\nSource summary"]
    bank.add(candidates, plan, vectors=[[1, 0, 0], [0, 1, 0]])
    bank.export(tmp_path / "questions.md")
    tokens = MarkdownIt().parse((tmp_path / "questions.md").read_text())
    assert sum(t.type == "heading_open" and t.tag == "h2" for t in tokens) == 2


def test_failed_vector_write_does_not_publish_or_reserve_titles(env):
    bank, index, plan, candidates = setup_bank(env)
    index.write_failure = True
    with pytest.raises(ServiceError):
        bank.add(candidates, plan, vectors=[[1, 0, 0], [0, 1, 0]])
    assert bank.stats()["total"] == 0 and not bank.keys and not bank.vectors
    index.write_failure = False
    assert bank.add(candidates, plan, vectors=[[1, 0, 0], [0, 1, 0]]) == 2


def test_failed_sql_publish_rolls_back_and_orphan_vectors_are_not_searchable(env):
    bank, index, plan, candidates = setup_bank(env)
    env.store.db.execute("""
        CREATE TRIGGER fail_catalog BEFORE INSERT ON question_catalog
        BEGIN SELECT RAISE(FAIL, 'test publish failure'); END
    """)
    with pytest.raises(Exception, match="test publish failure"):
        bank.add(candidates, plan, vectors=[[1, 0, 0], [0, 1, 0]])
    assert index.rows
    assert bank.stats()["total"] == 0
    assert env.store.db.execute("SELECT COUNT(*) FROM question_records").fetchone()[0] == 0
    assert bank.search("LangGraph") == []


def test_changed_evidence_is_not_returned_as_current_bank_result(env):
    bank, _, plan, candidates = setup_bank(env)
    bank.add(candidates, plan, vectors=[[1, 0, 0], [0, 1, 0]])
    env.store.db.execute("DELETE FROM active")
    assert bank.stats()["citations_active"] == 0
    assert bank.search("LangGraph") == []


def test_embedding_model_change_is_explicitly_rejected(env):
    bank, _, plan, candidates = setup_bank(env)
    bank.add(candidates, plan, vectors=[[1, 0, 0], [0, 1, 0]])
    changed = env.s.model_copy(update={"embedding_model": "another-model"})
    with pytest.raises(ValueError, match="embedding changed"):
        QuestionBank(changed, store=env.store, embeddings=env.embeddings, index=FakeIndex())


def test_explicit_topic_reclassification_preserves_questions_and_vectors(env):
    bank, index, plan, candidates = setup_bank(env)
    bank.add(candidates, plan, vectors=[[1, 0, 0], [0, 1, 0]])
    original = bank.rows()
    vectors = dict(index.rows)
    plan["version"] = "test-v2"
    plan["topics"][0]["quota"] = 0
    plan["topics"].append({
        "id": "runtime", "name": "运行时", "quota": 2, "hot": True,
        "title_patterns": ["langgraph"],
    })
    assert bank.adopt_topics(plan) == 2
    assert bank.counts["runtime"] == 2 and bank.counts["graph"] == 0
    assert {r["id"] for r in bank.rows()} == {r["id"] for r in original}
    assert index.rows == vectors and bank.stats()["citations_active"] == 2
    assert bank.adopt_topics(plan) == 0


def test_lower_quota_refuses_to_delete_saved_questions(env):
    bank, _, plan, candidates = setup_bank(env)
    bank.add(candidates, plan, vectors=[[1, 0, 0], [0, 1, 0]])
    plan["topics"][0]["quota"] = 1
    with pytest.raises(ValueError, match="no questions were removed"):
        bank.adopt_topics(plan)
    assert bank.stats()["total"] == 2 and bank.counts["graph"] == 2


def test_completed_build_resumes_without_models_and_keeps_prior_evidence(env, tmp_path, monkeypatch):
    import interview_question_bank.bank as module

    bank, index, plan, candidates = setup_bank(env)
    bank.add(candidates, plan, vectors=[[1, 0, 0], [0, 1, 0]])
    index._collection = "test_questions"
    previous = dict(
        model=env.s.llm_model, plan=plan, generation_batches=3,
        errors=[{"error_type": "HistoricalFailure"}], rejected={},
    )
    (tmp_path / "report.json").write_text(json.dumps(previous))
    monkeypatch.setattr(module, "QuestionBank", lambda _: bank)
    monkeypatch.setattr(module, "load_plan", lambda *args: plan)

    def forbidden(*args):
        raise AssertionError("Completed bank must not regenerate or re-extract")

    monkeypatch.setattr(module, "extract_candidates", forbidden)
    monkeypatch.setattr(module, "Models", forbidden)
    result = module.build_bank(env.s, tmp_path, target=2)
    assert result["complete"] and result["generation_batches"] == 3
    assert result["errors"] == previous["errors"] and len(result["resumes"]) == 1
    assert (tmp_path / "questions.jsonl").exists()


def test_generation_keeps_only_individually_verified_evidence_bound_questions(env, monkeypatch):
    import interview_question_bank.bank as module

    _, _, _, candidates = setup_bank(env)
    first, second = [c.question for c in candidates]
    forged = second.model_copy(update={"evidence_ids": ["invented"]})
    invented_quote = second.model_copy(update={"source_question_quote": "A heading not in evidence"})
    model = SimpleNamespace(
        draft=lambda *args: Draft(questions=[first, second, forged, invented_quote]),
        verify=lambda *args: Verification(
            sufficient=False, reason="Only first and forged were nominated",
            supported_question_numbers=[1, 3, 4],
        ),
    )
    monkeypatch.setattr(module, "Models", lambda _: model)
    result = module.generate_batch(
        env.s, InterviewRequest(topic="LangGraph", query="checkpoint"),
        candidates[0].chunks,
    )
    assert result["questions"] == [first]
    assert result["draft_count"] == 4 and not result["verification"]["sufficient"]
