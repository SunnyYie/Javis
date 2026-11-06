import hashlib
import json
import time
from dataclasses import asdict, dataclass, field


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
    contexts: list[dict] = field(default_factory=list)
    retrieval: list[dict] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


class ServiceError(RuntimeError):
    def __init__(self, service: str, category: str):
        self.service = service
        self.category = category
        super().__init__(f"{service}: {category}")
