from datetime import date

import pytest

from job_agent.config import Settings
from job_agent.models import (
    Education,
    Evidence,
    JobPreferences,
    Project,
    ResumePage,
    ResumeProfile,
    SemanticMatch,
    WorkExperience,
)
from job_agent.resume import months_worked
from job_agent.service import Service


class MemoryCache:
    def __init__(self, settings=None, sid="test"):
        from job_agent.models import digest
        self.digest = digest
        self.sid = sid
        self.data = {}
        self.warnings = set()

    def key(self, kind, value):
        return self.sid + kind + self.digest(value)

    def get(self, key):
        return self.data.get(key)

    def set(self, key, value):
        self.data[key] = value

    def purge(self):
        self.data.clear()


class FakeModels:
    """Explicit deterministic test double, never live-model evidence."""

    calls = 0
    fail = False
    bad_evidence = False

    def identity(self):
        return {"model": "test-double", "base_url": "not-a-provider"}

    def structured(self, schema, task, payload):
        self.calls += 1
        if self.fail:
            raise RuntimeError("intentional test-double failure")
        assert schema is SemanticMatch
        return SemanticMatch(
            level="满足条件", reason="测试替身的固定判定，不是真实模型",
            resume_quotes=["虚构活动工具链" if not self.bad_evidence else "不存在的证据"],
            job_quotes=["React TypeScript"],
        )


@pytest.fixture
def profile():
    evidence = Evidence(page=1, quote="样例公司 前端开发 2024.08 - 至今")
    work = [WorkExperience(
        company="样例公司", role="前端开发", start="2024-08", end="present", evidence=evidence,
    )]
    return ResumeProfile(
        id="test-profile", source_file="synthetic.md",
        summary="虚构活动工具链", skills=["React", "TypeScript"],
        work=work,
        projects=[Project(
            name="虚构活动工具链", description="虚构活动工具链", skills=["React"],
            evidence=Evidence(page=1, quote="虚构活动工具链 React"),
        )],
        education=[Education(
            school="虚构大学", major="软件工程", degree="本科",
            evidence=Evidence(page=1, quote="虚构大学 软件工程 本科"),
        )],
        pages=[ResumePage(page=1, method="text", text="虚构活动工具链 React TypeScript")],
        work_months=months_worked(work, date(2026, 11, 5)),
        as_of="2026-11-05", parser_version="test",
    )


@pytest.fixture
def prefs():
    return JobPreferences(roles=["前端"], cities=["杭州"], target_monthly_k=25)


@pytest.fixture
def models():
    return FakeModels()


@pytest.fixture
def cache():
    return MemoryCache()


@pytest.fixture
def service(tmp_path, monkeypatch, profile, prefs):
    monkeypatch.setattr("job_agent.service.Cache", MemoryCache)
    service = Service(Settings(_env_file=None, data_dir=tmp_path / "data"), "test-session",
                      emit=lambda *_: None)
    state = service.state()
    state["resume"] = profile.model_dump()
    state["preferences"] = prefs.model_dump()
    state["remote_allowed"] = True
    service.storage.save_state(service.sid, state)
    service.snapshot(presented=True)
    service.confirm()
    service.models = FakeModels()
    return service
