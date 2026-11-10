import re
from typing import Literal

from javis_core.models import (  # noqa: F401
    BrainError,
    StrictModel,
    ToolResult,
    digest,
    now,
    uid,
)
from pydantic import Field


def private(text):
    from job_agent.privacy import EMAIL, ID_NUMBER, PHONE

    text = ID_NUMBER.sub("[证件已隐藏]", text)
    text = PHONE.sub("[电话已隐藏]", text)
    text = EMAIL.sub("[邮箱已隐藏]", text)
    return re.sub(
        r"(?m)^\s*(姓名|联系人|微信|住址|通讯地址)\s*[:：].*$", "[身份信息已隐藏]", text,
    )

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
