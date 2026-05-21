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
    near_duplicate_group_id: int | None = None
    near_duplicate_group_size: int = 1
    candidate_group_id: int | None = None
    candidate_group_size: int = 1
    category_label: str = "General"
    category_description: str = ""
    task_family_id: str = "general-assistance"
    task_family: str = "General assistance"
    domain: str = "General"
    skills: list[str] = Field(default_factory=list)
    family_fit: str = "strong"
    difficulty: str = "medium"
    specificity: str = "generalizable"
    family_size: int = 1
    family_importance: float = 0.0
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
    task_family_id: str = "general-assistance"
    task_family: str = "General assistance"
    domain: str = "General"
    skills: list[str] = Field(default_factory=list)
    difficulty: str = "medium"
    specificity: str = "generalizable"
    checklist: list[str]


class BenchmarkMetadata(BaseModel):
    version: int
    benchmark_hash: str
    task_count: int
    task_family_count: int = 0
    domain_distribution: dict[str, int] = Field(default_factory=dict)
    family_distribution: dict[str, int] = Field(default_factory=dict)
    difficulty_distribution: dict[str, int] = Field(default_factory=dict)
    specificity_distribution: dict[str, int] = Field(default_factory=dict)
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
