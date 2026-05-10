from __future__ import annotations

from pathlib import Path

from egobench.ingest.base import IngestAdapter
from egobench.ingest.chatgpt import ChatGPTAdapter
from egobench.ingest.claude import ClaudeAdapter
from egobench.ingest.jsonl import JSONLAdapter


ADAPTERS: dict[str, type[IngestAdapter]] = {
    "chatgpt": ChatGPTAdapter,
    "claude": ClaudeAdapter,
    "jsonl": JSONLAdapter,
}


def resolve_adapter(name: str, path: Path) -> IngestAdapter:
    if name != "auto":
        try:
            return ADAPTERS[name]()
        except KeyError as exc:
            valid = ", ".join(["auto", *ADAPTERS])
            raise ValueError(f"Unknown adapter '{name}'. Valid adapters: {valid}") from exc
    for adapter_cls in ADAPTERS.values():
        if adapter_cls.detect(path):
            return adapter_cls()
    raise ValueError(f"Could not detect export format for {path}")

