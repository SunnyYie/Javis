import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from typing import Literal

from pydantic import BaseModel, Field


def digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


@dataclass
class Document:
    source: str
    url: str
    text: str
    path: str = ""
    repo: str = ""
    commit: str = ""
    license: str = "unknown"
    role: str = "unverified"
    fetched_at: float = field(default_factory=time.time)


@dataclass
class Chunk:
    id: str
    source: str
    text: str
    url: str
    path: str
    start: int
    end: int
    fetched_at: float
    expires_at: float
    repo: str = ""
    commit: str = ""
    license: str = "unknown"
    role: str = "unverified"
    content_hash: str = ""

    def to_dict(self):
        return asdict(self)


@dataclass
class Bundle:
    source: str
    documents: list[Document]
    coverage: dict
    repo: str = ""
    commit: str = ""
    # A partial response must never replace a previously complete snapshot.
    complete: bool = True


@dataclass
class Answer:
    text: str
    citations: list[dict]
    route: str
    sufficient: bool
    persisted: bool = True
    warnings: list[str] = field(default_factory=list)
    questions: list[dict] = field(default_factory=list)
    request: dict = field(default_factory=dict)
    explanation: str = ""
    hot_count: int = 0
    cache_promoted: bool = False
    retrieval: dict = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)


class ServiceError(RuntimeError):
    def __init__(self, service: str, category: str):
        self.service = service
        self.category = category
        super().__init__(f"{service}: {category}")


Difficulty = Literal["基础", "进阶", "高阶"]


class InterviewRequest(BaseModel):
    query: str = Field(min_length=1)
    topic: str = ""
    knowledge_points: list[str] = Field(default_factory=list)
    count: int = Field(default=5, ge=1, le=20)
    difficulties: list[Difficulty] = Field(default_factory=lambda: ["基础", "进阶", "高阶"])
    mode: Literal["questions", "explain"] = "questions"
    target_number: int | None = Field(default=None, ge=1)
    excluded_titles: list[str] = Field(default_factory=list)


class InterviewQuestion(BaseModel):
    title: str = Field(min_length=1)
    difficulty: Difficulty
    knowledge_points: list[str] = Field(min_length=1)
    answer: str = Field(min_length=1)
    key_points: list[str] = Field(min_length=1)
    origin: Literal["来源原题", "基于资料生成"]
    evidence_ids: list[str] = Field(min_length=1)
    # An exact source excerpt is required before the original-question label is accepted.
    source_question_quote: str = ""


class Draft(BaseModel):
    questions: list[InterviewQuestion] = Field(default_factory=list)
    explanation: str = ""
    explanation_evidence_ids: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)


class Coverage(BaseModel):
    sufficient: bool = False
    reason: str = ""
    queries: list[str] = Field(default_factory=list)


class Verification(BaseModel):
    supported_question_numbers: list[int] = Field(default_factory=list)
    explanation_supported: bool = False
    sufficient: bool = False
    reason: str = ""


def difficulty_targets(request: InterviewRequest) -> dict[str, int]:
    if request.mode == "explain":
        return {}
    choices = list(dict.fromkeys(request.difficulties)) or ["基础", "进阶", "高阶"]
    if choices == ["基础", "进阶", "高阶"] and request.count == 5:
        return {"基础": 2, "进阶": 2, "高阶": 1}
    return {
        level: request.count // len(choices) + (i < request.count % len(choices))
        for i, level in enumerate(choices)
    }
