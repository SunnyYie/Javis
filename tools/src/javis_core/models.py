import hashlib
import json
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


class Capability(StrictModel):
    team_id: str
    description: str
    input_constraints: list[str]
    authorization: list[str]


class TaskRequest(StrictModel):
    task_id: str = Field(default_factory=lambda: uid("task_"))
    created_at: str = Field(default_factory=now)
    parent_task_id: str | None = None
    session_id: str
    team_id: str
    goal: str = Field(min_length=1, max_length=12000)
    constraints: dict = Field(default_factory=dict)
    authorization_refs: list[str] = Field(default_factory=list)
    budget: dict = Field(default_factory=lambda: {"seconds": 180, "vigo_calls": 5})


class TaskResult(ToolResult):
    task_id: str
    progress: Literal[
        "queued", "running", "waiting_user", "paused", "completed", "cancelled",
    ] = "completed"
    children: list[str] = Field(default_factory=list)
    coverage: dict = Field(default_factory=dict)
    pending_action: dict | None = None
