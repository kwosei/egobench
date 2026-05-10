from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from egobench.ingest.base import Conversation, IngestAdapter, Turn, load_json_path, normalize_role, text_from_content


class ClaudeAdapter(IngestAdapter):
    name = "claude"

    @classmethod
    def detect(cls, path: Path) -> bool:
        try:
            data = load_json_path(path)
        except Exception:
            return False
        sample = data[0] if isinstance(data, list) and data else data
        return isinstance(sample, dict) and ("chat_messages" in sample or sample.get("account") == "claude")

    def load(self, path: Path) -> list[Conversation]:
        data = load_json_path(path)
        records = data.get("conversations", []) if isinstance(data, dict) else data
        conversations: list[Conversation] = []
        for idx, record in enumerate(records):
            if not isinstance(record, dict):
                continue
            raw_messages = record.get("chat_messages") or record.get("messages") or []
            turns = self._turns(raw_messages)
            if not turns:
                continue
            conv_id = str(record.get("uuid") or record.get("id") or self._stable_id(idx, turns))
            conversations.append(
                Conversation(
                    id=conv_id,
                    turns=turns,
                    model_used=record.get("model"),
                    metadata={"name": record.get("name")},
                )
            )
        return conversations

    def _turns(self, messages: list[Any]) -> list[Turn]:
        turns: list[Turn] = []
        for raw in messages:
            if not isinstance(raw, dict):
                continue
            text = text_from_content(raw.get("text") or raw.get("content"))
            if not text.strip():
                continue
            turns.append(
                Turn(
                    role=normalize_role(raw.get("sender") or raw.get("role")),
                    text=text.strip(),
                    ts=raw.get("created_at") or raw.get("updated_at"),
                )
            )
        return turns

    def _stable_id(self, idx: int, turns: list[Turn]) -> str:
        blob = "\n".join(f"{turn.role}:{turn.text}" for turn in turns)
        return f"claude-{idx}-{hashlib.sha256(blob.encode()).hexdigest()[:12]}"

