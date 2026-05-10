from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Turn:
    role: str
    text: str
    ts: str | None = None


@dataclass(frozen=True)
class Conversation:
    id: str
    turns: list[Turn]
    model_used: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class IngestAdapter(ABC):
    name: str

    @classmethod
    @abstractmethod
    def detect(cls, path: Path) -> bool:
        raise NotImplementedError

    @abstractmethod
    def load(self, path: Path) -> list[Conversation]:
        raise NotImplementedError


def load_json_path(path: Path) -> Any:
    if path.is_dir():
        candidates = [
            path / "conversations.json",
            path / "chat.json",
            path / "messages.json",
        ]
        for candidate in candidates:
            if candidate.exists():
                return json.loads(candidate.read_text(encoding="utf-8"))
        raise FileNotFoundError(f"No supported JSON export found under {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_role(role: str | None) -> str:
    role = (role or "user").lower()
    if role in {"human", "customer"}:
        return "user"
    if role in {"assistant", "bot", "ai"}:
        return "assistant"
    if role in {"system", "tool"}:
        return role
    return "user"


def text_from_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(filter(None, (text_from_content(item) for item in content))).strip()
    if isinstance(content, dict):
        if "text" in content:
            return text_from_content(content["text"])
        if "parts" in content:
            return text_from_content(content["parts"])
        if "content" in content:
            return text_from_content(content["content"])
        if content.get("type") == "text":
            return str(content.get("text", ""))
    return str(content)


def first_user_text(turns: list[Turn]) -> str:
    for turn in turns:
        if turn.role == "user" and turn.text.strip():
            return turn.text.strip()
    return ""

