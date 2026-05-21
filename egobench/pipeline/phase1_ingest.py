from __future__ import annotations

from pathlib import Path

from rich.console import Console

from egobench.db import DB, insert_conversations
from egobench.ingest.registry import resolve_adapter


def run(db: DB, path: Path, adapter_name: str = "auto", console: Console | None = None) -> dict:
    console = console or Console()
    console.print(f"[dim]phase1: resolving adapter for {path}[/dim]")
    adapter = resolve_adapter(adapter_name, path)
    console.print(f"[dim]phase1: loading export with {adapter.name} adapter[/dim]")
    conversations = adapter.load(path)
    console.print(f"[dim]phase1: importing {len(conversations)} conversations[/dim]")
    count = insert_conversations(db, conversations, adapter.name)
    return {"phase": 1, "adapter": adapter.name, "conversations": count}
