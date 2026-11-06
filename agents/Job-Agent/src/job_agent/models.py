from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def now() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()


def digest(value) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


class AgentError(Exception):
    def __init__(self, code: str, message: str = ""):
        self.code = code
        self.message = message or code
        super().__init__(self.message)


class Evidence(BaseModel):
    page: int = Field(ge=1)
    quote: str = Field(min_length=1)


class ResumePage(BaseModel):
    page: int
    method: Literal["text", "ocr"]
    text: str
    blocks: list[dict] = Field(default_factory=list)
    confidence: float | None = None


class WorkExperience(BaseModel):
    company: str
    role: str
    start: str = Field(description="YYYY-MM, or empty if unknown")
    end: str = Field(description="YYYY-MM, present, or empty if unknown")
    evidence: Evidence

    @field_validator("start", "end")
    @classmethod
    def valid_month(cls, value: str) -> str:
        if value in ("", "present"):
            return value
        if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", value):
            raise ValueError("Expected YYYY-MM")
        return value


class Project(BaseModel):
    name: str
    description: str
    skills: list[str]
    evidence: Evidence


class Education(BaseModel):
    school: str
    major: str
    degree: str
    evidence: Evidence


class ExtractedProfile(BaseModel):
    summary: str
    skills: list[str]
    work: list[WorkExperience]
    projects: list[Project]
    education: list[Education]
    uncertainties: list[str] = Field(default_factory=list)


class ResumeProfile(ExtractedProfile):
    id: str
    source_file: str
    pages: list[ResumePage]
    work_months: int
    as_of: str
    parser_version: str


class JobPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid")
    roles: list[str] = Field(default_factory=list, max_length=8)
    cities: list[str] = Field(default_factory=list, max_length=8)
    target_monthly_k: float | None = Field(default=None, gt=0, le=1000)
    excluded_terms: list[str] = Field(default_factory=list, max_length=30)

    @field_validator("roles", "cities", "excluded_terms")
    @classmethod
    def clean_list(cls, values):
        result = list(dict.fromkeys(v.strip() for v in values if v.strip()))
        if any(len(v) > 100 for v in result):
            raise ValueError("Condition too long")
        return result

    def missing(self) -> list[str]:
        return [
            label
            for key, label in (
                ("roles", "期望岗位"),
                ("cities", "工作城市"),
                ("target_monthly_k", "目标税前月薪（K）"),
            )
            if not getattr(self, key)
        ]


class Salary(BaseModel):
    raw: str = ""
    min_k: float | None = None
    max_k: float | None = None
    period: Literal["month", "year", "day", "hour", "unknown"] = "unknown"
    currency: str = "unknown"
    months: int | None = None


class JobPosting(BaseModel):
    id: str
    source: Literal["boss_live", "ohmyboss_import"]
    title: str
    company: str = ""
    city: str = ""
    location: str = ""
    url: str = ""
    salary: Salary = Field(default_factory=Salary)
    experience: str = ""
    education: str = ""
    description: str = ""
    fetched_at: str | None = None
    imported_at: str | None = None
    source_file: str | None = None
    detail_complete: bool = False
    raw: dict = Field(default_factory=dict)


MatchLevel = Literal["满足条件", "可尝试", "不符合", "信息不足", "评估失败"]


class SemanticMatch(BaseModel):
    level: Literal["满足条件", "可尝试", "不符合", "信息不足"]
    reason: str
    resume_quotes: list[str] = Field(default_factory=list)
    job_quotes: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def needs_evidence(self):
        if self.level in ("满足条件", "可尝试"):
            if not self.resume_quotes or not self.job_quotes:
                raise ValueError("Positive recommendations require both evidence sources")
        return self


class MatchResult(BaseModel):
    job_id: str
    level: MatchLevel
    reasons: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    pending: list[str] = Field(default_factory=list)
    resume_quotes: list[str] = Field(default_factory=list)
    job_quotes: list[str] = Field(default_factory=list)
    preferences_hash: str
    evaluated_at: str = Field(default_factory=now)
    error: str | None = None
    cached: bool = False


class SearchRun(BaseModel):
    id: str
    source: str
    started_at: str = Field(default_factory=now)
    finished_at: str | None = None
    status: Literal["running", "complete", "partial", "failed", "interrupted"] = "running"
    job_ids: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    conditions_hash: str
    limit: int
