import hashlib
import json
import re
import uuid
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field


def now():
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()


def uid(prefix=""):
    return prefix + uuid.uuid4().hex[:16]


def digest(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def private(text):
    from job_agent.privacy import EMAIL, ID_NUMBER, PHONE

    text = ID_NUMBER.sub("[证件已隐藏]", text)
    text = PHONE.sub("[电话已隐藏]", text)
    text = EMAIL.sub("[邮箱已隐藏]", text)
    return re.sub(
        r"(?m)^\s*(姓名|联系人|微信|住址|通讯地址)\s*[:：].*$", "[身份信息已隐藏]", text,
    )


class BrainError(Exception):
    def __init__(self, code, message=""):
        self.code, self.message = code, message or code
        super().__init__(self.message)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ToolResult(StrictModel):
    status: Literal["ok", "partial", "blocked", "failed"] = "ok"
    data: dict = Field(default_factory=dict)
    citations: list[dict] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    artifacts: list[dict] = Field(default_factory=list)
    error: str | None = None


Section = Literal["summary", "skills", "work", "projects", "education", "achievements"]


class FactInput(StrictModel):
    section: Section
    text: str = Field(min_length=1, max_length=4000)


class FactSelection(StrictModel):
    heading: str
    fact_ids: list[str] = Field(min_length=1)


class Rewrite(StrictModel):
    fact_id: str
    text: str = Field(min_length=1, max_length=4000)
    reason: str


class ResumePlan(StrictModel):
    sections: list[FactSelection] = Field(min_length=1, max_length=12)
    diagnosis: list[str] = Field(default_factory=list)
    rewrites: list[Rewrite] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)


class Dimension(StrictModel):
    name: Literal["正确性或项目证据", "完整性", "表达结构"]
    score: float | None = Field(default=None, ge=0, le=4)
    reason: str
    answer_quote: str = ""
    answer_segment_id: int | None = Field(default=None, ge=1)


class Assessment(StrictModel):
    dimensions: list[Dimension] = Field(min_length=3, max_length=3)
    strengths: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    followup_needed: bool = False


class LearningPlan(StrictModel):
    tasks: list[str] = Field(min_length=1, max_length=12)
    rationale: str


class Observation(StrictModel):
    quote: str = Field(min_length=1)
    analysis: str


class TranscriptReview(StrictModel):
    observations: list[Observation]
    learning_tasks: list[str]
    unknowns: list[str]


class Probe(StrictModel):
    ok: bool
