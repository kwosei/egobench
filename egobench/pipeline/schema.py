from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class TurnModel(BaseModel):
    role: str
    text: str
    ts: str | None = None


class TaskCandidate(BaseModel):
    conversation_id: str
    first_user_text: str
    cluster_id: int | None = None
    cluster_size: int = 1
    category_label: str = "General"
    category_description: str = ""
    importance: float = 0.0
    selected: bool = False
    checklist: list[str] = Field(default_factory=list)
    raw_checklists: dict[str, list[str]] = Field(default_factory=dict)


class BenchmarkTask(BaseModel):
    id: str
    conversation_id: str
    turns: list[TurnModel]
    category: str
    category_description: str
    cluster_id: int
    cluster_size: int
    importance: float
    checklist: list[str]


class BenchmarkMetadata(BaseModel):
    version: int
    benchmark_hash: str
    task_count: int
    generated_at: str
    seed: int
    config: dict[str, Any]


class Benchmark(BaseModel):
    metadata: BenchmarkMetadata
    tasks: list[BenchmarkTask]


def canonical_json(value: Any) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def stable_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

