import copy
from pathlib import Path

import pytest
from job_agent.config import Settings as JobSettings
from job_agent.service import Service as JobService

from brain_leader.config import Settings
from brain_leader.models import Assessment, LearningPlan, ResumePlan, TranscriptReview
from brain_leader.service import Service


class FakeModels:
    """Explicit test double; never used by production or opt-in live tests."""

    def __init__(self):
        self.calls = []
        self.invalid_quote = False
        self.fail = False
        self.followup = False

    def structured(self, schema, task, payload):
        self.calls.append((schema.__name__, payload))
        if self.fail:
            raise RuntimeError("synthetic failure")
        if schema is ResumePlan:
            return ResumePlan(
                sections=[{"heading": "不可信标题", "fact_ids": [f["id"] for f in payload["facts"]]}],
                diagnosis=["需要增加可核验成果"], missing=["项目量化成果"],
                rewrites=[{"fact_id": payload["facts"][0]["id"],
                           "text": "负责 React 页面和可复用组件开发。", "reason": "表达调整"}],
            )
        if schema is Assessment:
            return Assessment(
                dimensions=[{
                    "name": name, "score": 2,
                    "reason": "仅做测试分支验证",
                    "answer_quote": "not present" if self.invalid_quote else payload["answer"][:6],
                } for name in ("正确性或项目证据", "完整性", "表达结构")],
                gaps=["需要说明验证方法"], followup_needed=self.followup,
            )
        if schema is LearningPlan:
            return LearningPlan(tasks=["复习 React 并重新作答"], rationale="测试")
        if schema is TranscriptReview:
            return TranscriptReview(
                observations=[{"quote": payload["transcript"][:5], "analysis": "测试分析"}],
                learning_tasks=["补充验证方法"], unknowns=["面试官意图未知"],
            )
        raise AssertionError(schema)


class FakeAdapters:
    def __init__(self, settings, sid):
        self.job = JobService(JobSettings(
            _env_file=None, data_dir=settings.data_dir / "jobs",
            redis_url="redis://127.0.0.1:1/0",
            llm_api_key="test-only", llm_model="test-only",
        ), sid, emit=lambda _: None)
        self.answer = {
            "text": "测试题单", "route": "local", "sufficient": True, "persisted": True,
            "citations": [{"id": "e1", "text": "明确标注的测试依据", "source": "test-only"}],
            "questions": [{
                "title": "React 状态如何更新？", "difficulty": "基础",
                "answer": "PRIVATE_REFERENCE", "key_points": ["state", "render"],
                "knowledge_points": ["React"], "evidence_ids": ["e1"], "origin": "基于资料生成",
            }],
            "warnings": [],
        }
        self.requests = []

    def ask(self, kind, question, history=None, local_only=False):
        self.requests.append((kind, question))
        return copy.deepcopy(self.answer)

    def close(self):
        pass


@pytest.fixture
def service(tmp_path):
    settings = Settings(
        _env_file=None, data_dir=tmp_path / "runtime", memory_dir=tmp_path / "memory",
        llm_env_file=tmp_path / "absent.env", llm_api_key="test-only",
        skills_dir=Path(__file__).resolve().parents[2] / "skills",
    )
    sid = "unit"
    value = Service(settings, sid, adapters=FakeAdapters(settings, sid), models=FakeModels())
    yield value
    value.close()


@pytest.fixture
def consented(service):
    service.consent()
    return service
