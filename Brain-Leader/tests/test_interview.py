import json

import pytest

from brain_leader.cli import command, run
from brain_leader.models import BrainError
from brain_leader.service import Service


def prepare(service, mode="technical", followups=0):
    service.resumes.add_fact("projects", "我用React开发了管理页面。", confirmed=True)
    service.questions("React", 1)
    return service.interviews.prepare(mode, 1, followups)


@pytest.mark.parametrize("mode", ["technical", "project", "behavioral", "mixed"])
def test_all_modes_hide_answers_until_finish(consented, mode):
    public = prepare(consented, mode)
    assert "PRIVATE_REFERENCE" not in json.dumps(public)
    assert "PRIVATE_REFERENCE" not in json.dumps(consented.snapshot())
    with pytest.raises(BrainError):
        consented.interviews.review()
    result = consented.interviews.answer(public["token"], "我先验证状态变化和渲染结果，再说明边界。")
    assert result["interview"]["status"] == "awaiting_finish"
    assert "PRIVATE_REFERENCE" not in json.dumps(result)
    consented.interviews.control("finish")
    review = consented.review()
    assert review["rows"]
    assert review["scale"] == "0-4"
    if mode == "behavioral":
        assert review["coverage"] == pytest.approx(2 / 3)


def test_same_token_is_idempotent_and_conflicting_payload_rejected(consented):
    public = prepare(consented)
    first = consented.interviews.answer(public["token"], "验证状态更新")
    duplicate = consented.interviews.answer(public["token"], "验证状态更新")
    assert duplicate["duplicate"]
    assert first["interview"] == duplicate["interview"]
    assert len(consented.interviews.current()["answers"]) == 1
    with pytest.raises(BrainError, match="相同提交"):
        consented.interviews.answer(public["token"], "不同回答")


def test_invalid_model_quote_or_failure_does_not_advance(consented):
    public = prepare(consented)
    consented.models.invalid_quote = True
    with pytest.raises(BrainError):
        consented.interviews.answer(public["token"], "验证状态更新")
    assert not consented.interviews.current()["answers"]
    assert consented.interviews.current()["token"] == public["token"]
    consented.models.invalid_quote = False
    consented.models.fail = True
    with pytest.raises(RuntimeError):
        consented.interviews.answer(public["token"], "验证状态更新")
    assert not consented.interviews.current()["answers"]


def test_behavioral_unknown_evidence_is_unscored_before_quote_validation(consented):
    public = prepare(consented, "behavioral")
    original = consented.models.structured

    def assessment(schema, task, payload):
        value = original(schema, task, payload)
        value.dimensions[0].answer_quote = "not candidate words"
        return value

    consented.models.structured = assessment
    consented.interviews.answer(public["token"], "我通过复现问题和请求日志定位了重复触发。")
    consented.interviews.control("finish")
    review = consented.interviews.review()
    dimension = review["rows"][0]["answers"][0]["assessment"]["dimensions"][0]
    assert dimension["score"] is None
    assert dimension["answer_quote"] == ""
    assert review["coverage"] == pytest.approx(2 / 3)


def test_segment_reference_is_resolved_from_candidate_words(consented):
    public = prepare(consented)
    original = consented.models.structured

    def assessment(schema, task, payload):
        value = original(schema, task, payload)
        for dimension in value.dimensions:
            dimension.answer_segment_id = 2
            dimension.answer_quote = "不得发布的模型改写"
        return value

    consented.models.structured = assessment
    consented.interviews.answer(public["token"], "我先检查请求。然后验证失败状态。")
    saved = consented.interviews.current()["answers"][0]["assessment"]
    assert all(d["answer_quote"] == "然后验证失败状态。" for d in saved["dimensions"])


def test_unknown_answer_segment_is_retried_but_never_advances(consented):
    public = prepare(consented)
    original = consented.models.structured
    calls = []

    def assessment(schema, task, payload):
        calls.append(dict(payload))
        value = original(schema, task, payload)
        value.dimensions[1].answer_segment_id = 999
        return value

    consented.models.structured = assessment
    with pytest.raises(BrainError):
        consented.interviews.answer(public["token"], "我验证状态。")
    assert len(calls) == 2
    assert "validation_feedback" in calls[1]
    assert not consented.interviews.current()["answers"]
    assert consented.interviews.current()["token"] == public["token"]


def test_pause_resume_after_new_service_instance(consented):
    public = prepare(consented)
    consented.interviews.control("pause")
    recovered = Service(
        consented.settings, consented.sid, adapters=consented.adapters, models=consented.models,
    )
    assert recovered.interviews.current()["status"] == "paused"
    with pytest.raises(BrainError):
        recovered.interviews.answer(public["token"], "测试")
    recovered.interviews.control("resume")
    assert recovered.interviews.public()["token"] == public["token"]
    assert recovered.interviews.answer(public["token"], "测试状态更新")["interview"]["status"] == "awaiting_finish"


def test_mixed_three_questions_freezes_all_three_kinds(consented):
    prepare(consented, "project")
    consented.interviews.control("finish")
    public = consented.interviews.prepare("mixed", 3, 0)
    assert [q["kind"] for q in consented.interviews.current()["questions"]] == [
        "technical", "project", "behavioral",
    ]
    for index in range(3):
        assert public["question_number"] == index + 1
        assert "PRIVATE_REFERENCE" not in json.dumps(public)
        public = consented.interviews.answer(public["token"], "我用请求日志定位重复触发并验证边界。")["interview"]
    assert public["status"] == "awaiting_finish"
    consented.interviews.control("finish")
    assert consented.interviews.review()["coverage"] == pytest.approx(8 / 9)


def test_followups_bounded_and_not_scored_as_extra_weight(consented):
    public = prepare(consented, followups=2)
    consented.models.followup = True
    for _ in range(3):
        result = consented.interviews.answer(public["token"], "我验证了状态更新的边界")
        public = result["interview"]
    assert public["status"] == "awaiting_finish"
    assert len(consented.interviews.current()["answers"]) == 3
    consented.interviews.control("finish")
    review = consented.interviews.review()
    assert review["coverage"] == 1
    assert review["score"] == 2


@pytest.mark.parametrize("text", [
    "/questions React", "/knowledge 告诉我参考答案", "/draft", "/review",
    "/export review", "/memory", "/links", "/prefs {}",
])
def test_interview_blocks_side_channels(consented, text):
    prepare(consented)
    result = run(consented, text)
    assert result["error"] == "interview_in_progress"
    assert "PRIVATE_REFERENCE" not in json.dumps(result)


def test_future_tools_are_blocked_during_interview(consented):
    prepare(consented)
    result = consented.tool("generate_questions", topic="React")
    assert result["error"] == "interview_in_progress"
    assert "PRIVATE_REFERENCE" not in json.dumps(result)


def test_project_requires_real_confirmed_fact(consented):
    with pytest.raises(BrainError):
        consented.interviews.prepare("project", 1)


def test_partial_technical_basis_does_not_start(consented):
    consented.adapters.answer["sufficient"] = False
    consented.questions("React", 1)
    with pytest.raises(BrainError):
        consented.interviews.prepare("technical", 1)
    assert consented.interviews.current() is None


def test_early_finish_reports_coverage_not_fake_zero(consented):
    prepare(consented)
    command(consented, "/finish")
    result = command(consented, "/review")
    assert result["score"] is None
    assert result["coverage"] == 0


@pytest.mark.parametrize("mode", ["project", "behavioral"])
def test_multi_question_practice_does_not_repeat_identical_prompts(consented, mode):
    consented.resumes.add_fact("projects", "我负责React组件和接口。", confirmed=True)
    consented.interviews.prepare(mode, 5, 0)
    titles = [q["title"] for q in consented.interviews.current()["questions"]]
    assert len(set(titles)) == 5
