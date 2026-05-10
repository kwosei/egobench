from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class Completion:
    text: str
    model: str
    usage: Usage
    billable: bool = True

    def json(self) -> dict:
        return json.loads(self.text)


class LLMClient(Protocol):
    model: str

    def complete(self, prompt: str, *, temperature: float = 0.0) -> Completion:
        ...


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, int(len(text.split()) * 1.35) + 1)
