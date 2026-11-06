import json
from types import SimpleNamespace

import pytest
from conftest import bundle

from interview_question_bank.evaluation import (
    gold_ids,
    isolate,
    load_cases,
    retrieval_metrics,
    validate_answer,
)
from interview_question_bank.storage import Store


def test_dataset_rejects_output_path_traversal_and_duplicate_ids(tmp_path):
    path = tmp_path / "cases.json"
    base = dict(question="RRF", reference="Reference", evidence=[{"path": "a", "quote": "b"}])
    for cases in ([dict(base, id="../outside")], [dict(base, id="x"), dict(base, id="x")], []):
        path.write_text(json.dumps(cases))
        with pytest.raises(ValueError):
            load_cases(path)


def test_references_must_exist_in_real_chunk_text_and_ranking_metrics_are_exact(env):
    env.knowledge.ingest(bundle())
    chunks = env.store.active_chunks()
    case = {"id": "one", "evidence": [{"path": "README.md", "quote": "durable agent"}]}
    assert gold_ids(case, chunks) == {chunks[0].id}
    assert retrieval_metrics(["a", chunks[0].id], {chunks[0].id, "b"}) == {
        "gold_chunk_recall": 0.5, "reciprocal_rank": 0.5,
    }
    case["evidence"][0]["quote"] = "fabricated evidence"
    with pytest.raises(ValueError, match="Reference evidence missing"):
        gold_ids(case, chunks)


def test_evaluation_copy_isolates_index_corpus_sessions_and_preserves_normal_data(
    env, monkeypatch, tmp_path,
):
    from interview_question_bank import evaluation

    env.knowledge.ingest(bundle())
    sid = env.store.new_session()
    env.store.add_turn(sid, "question", "{}")
    corpus_id, version = env.store.corpus_id, env.store.version
    env.index._collection = "isolated-test"
    monkeypatch.setattr(evaluation, "Knowledge", lambda *args: SimpleNamespace(index=env.index))
    output = tmp_path / "evaluation"
    output.mkdir()
    case = {"id": "one", "evidence": [{"path": "README.md", "quote": "durable agent"}]}
    settings = isolate(env.s, output, [case])
    copy = Store(settings.data_dir)
    assert copy.corpus_id != corpus_id
    assert settings.index_namespace != env.s.index_namespace
    assert copy.sessions() == []
    assert env.store.sessions() == [sid]
    assert env.store.corpus_id == corpus_id and env.store.version == version
    assert len(copy.active_chunks()) == len(env.store.active_chunks()) == 1
    snapshot = json.loads(copy.sources()[0]["coverage"])["snapshot"]
    assert (copy.root / "snapshots" / f"{snapshot}.json").exists()


def test_e2e_validation_rejects_missing_trace_insufficient_and_forged_context():
    answer = {
        "sufficient": True, "persisted": True, "route": "local",
        "citations": [{"id": "source1"}],
        "questions": [{
            "title": "什么是 RRF", "difficulty": "基础", "knowledge_points": ["RRF"],
            "answer": "按排名融合", "key_points": ["排名"], "origin": "基于资料生成",
            "evidence_ids": ["source1"],
        }],
        "retrieval": {
            "reranker_enabled": True, "scores": [{"id": "source1", "score": 1}],
            "dense_ids": ["source1"], "lexical_ids": ["source1"], "context_ids": ["source1"],
        },
    }
    validate_answer(answer)
    answer["retrieval"]["context_ids"] = ["unrelated"]
    with pytest.raises(ValueError, match="actual generation contexts"):
        validate_answer(answer)
    answer["retrieval"]["context_ids"] = ["source1"]
    answer["retrieval"]["scores"] = []
    with pytest.raises(ValueError, match="hybrid retrieval"):
        validate_answer(answer)
    answer["sufficient"] = False
    with pytest.raises(ValueError, match="insufficient"):
        validate_answer(answer)
