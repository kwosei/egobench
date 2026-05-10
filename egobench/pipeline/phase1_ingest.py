from __future__ import annotations

from pathlib import Path

from egobench.db import DB, insert_conversations
from egobench.ingest.registry import resolve_adapter


def run(db: DB, path: Path, adapter_name: str = "auto") -> dict:
    adapter = resolve_adapter(adapter_name, path)
    conversations = adapter.load(path)
    count = insert_conversations(db, conversations, adapter.name)
    return {"phase": 1, "adapter": adapter.name, "conversations": count}

