from __future__ import annotations

from pathlib import Path

from rich.console import Console

from egobench.config import PrivacyCfg
from egobench.db import DB, insert_conversations
from egobench.ingest.registry import resolve_adapter
from egobench.privacy import make_redactor, redact_conversations


def run(
    db: DB,
    path: Path,
    adapter_name: str = "auto",
    console: Console | None = None,
    privacy: PrivacyCfg | None = None,
) -> dict:
    console = console or Console()
    console.print(f"[dim]phase1: resolving adapter for {path}[/dim]")
    adapter = resolve_adapter(adapter_name, path)
    console.print(f"[dim]phase1: loading export with {adapter.name} adapter[/dim]")
    conversations = adapter.load(path)
    redactions = 0
    if privacy and privacy.enabled:
        console.print(
            f"[dim]phase1: redacting PII with privacy backend '{privacy.backend}'[/dim]"
        )
        redactor = make_redactor(privacy)
        conversations, redactions = redact_conversations(conversations, redactor)
        console.print(f"[dim]phase1: redacted {redactions} PII spans[/dim]")
    console.print(f"[dim]phase1: importing {len(conversations)} conversations[/dim]")
    count = insert_conversations(db, conversations, adapter.name)
    return {
        "phase": 1,
        "adapter": adapter.name,
        "conversations": count,
        "redactions": redactions,
    }
