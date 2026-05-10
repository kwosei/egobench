from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from egobench.ingest.base import Conversation, IngestAdapter, Turn, load_json_path, normalize_role, text_from_content


class ChatGPTAdapter(IngestAdapter):
    name = "chatgpt"

    @classmethod
    def detect(cls, path: Path) -> bool:
        try:
            data = load_json_path(path)
        except Exception:
            return False
        sample = data[0] if isinstance(data, list) and data else data
        return isinstance(sample, dict) and ("mapping" in sample or "conversations" in sample)

    def load(self, path: Path) -> list[Conversation]:
        data = load_json_path(path)
        records = data.get("conversations", []) if isinstance(data, dict) else data
        conversations: list[Conversation] = []
        for idx, record in enumerate(records):
            if not isinstance(record, dict):
                continue
            turns = self._turns_from_record(record)
            if not turns:
                continue
            conv_id = str(record.get("id") or record.get("conversation_id") or self._stable_id(idx, turns))
            conversations.append(
                Conversation(
                    id=conv_id,
                    turns=turns,
                    model_used=record.get("model") or record.get("default_model_slug"),
                    metadata={"title": record.get("title")},
                )
            )
        return conversations

    def _turns_from_record(self, record: dict[str, Any]) -> list[Turn]:
        if "mapping" in record and isinstance(record["mapping"], dict):
            nodes = []
            for node in record["mapping"].values():
                message = node.get("message") if isinstance(node, dict) else None
                if not message:
                    continue
                content = text_from_content(message.get("content"))
                if not content.strip():
                    continue
                author = message.get("author") or {}
                nodes.append(
                    (
                        message.get("create_time") or node.get("create_time") or 0,
                        Turn(
                            role=normalize_role(author.get("role")),
                            text=content.strip(),
                            ts=str(message.get("create_time") or "") or None,
                        ),
                    )
                )
            nodes.sort(key=lambda item: (item[0] is None, item[0]))
            return [turn for _, turn in nodes if turn.role != "system"]

        raw_turns = record.get("turns") or record.get("messages") or []
        turns: list[Turn] = []
        for raw in raw_turns:
            if not isinstance(raw, dict):
                continue
            text = text_from_content(raw.get("content") or raw.get("text") or raw.get("message"))
            if text.strip():
                turns.append(Turn(normalize_role(raw.get("role") or raw.get("author")), text.strip(), raw.get("ts")))
        return turns

    def _stable_id(self, idx: int, turns: list[Turn]) -> str:
        blob = "\n".join(f"{turn.role}:{turn.text}" for turn in turns)
        return f"chatgpt-{idx}-{hashlib.sha256(blob.encode()).hexdigest()[:12]}"

