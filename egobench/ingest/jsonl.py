from __future__ import annotations

import json
from pathlib import Path

from egobench.ingest.base import Conversation, IngestAdapter, Turn, normalize_role, text_from_content


class JSONLAdapter(IngestAdapter):
    name = "jsonl"

    @classmethod
    def detect(cls, path: Path) -> bool:
        return path.is_file() and path.suffix.lower() == ".jsonl"

    def load(self, path: Path) -> list[Conversation]:
        conversations: list[Conversation] = []
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            raw = json.loads(line)
            turns = [
                Turn(
                    role=normalize_role(item.get("role")),
                    text=text_from_content(item.get("text") or item.get("content")).strip(),
                    ts=item.get("ts"),
                )
                for item in raw.get("turns", [])
                if text_from_content(item.get("text") or item.get("content")).strip()
            ]
            if turns:
                conversations.append(
                    Conversation(
                        id=str(raw.get("id") or f"jsonl-{line_no}"),
                        turns=turns,
                        model_used=raw.get("model_used"),
                        metadata=raw.get("metadata", {}),
                    )
                )
        return conversations

