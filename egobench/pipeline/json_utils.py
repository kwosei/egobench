from __future__ import annotations

import json
import re
from typing import Any


def parse_json_object(text: str) -> dict[str, Any]:
    last_error: Exception | None = None
    for candidate in _json_candidates(text):
        for variant in _json_variants(candidate):
            try:
                parsed = json.loads(variant)
            except json.JSONDecodeError as err:
                last_error = err
                continue
            if isinstance(parsed, dict):
                return parsed
            if isinstance(parsed, list):
                return {"checklists": parsed}
            last_error = ValueError("model response JSON must be an object or list")
    if last_error is not None:
        raise last_error
    raise ValueError("empty model response")


def _json_candidates(text: str) -> list[str]:
    stripped = (text or "").strip()
    if not stripped:
        raise ValueError("empty model response")

    candidates: list[str] = []
    _append_unique(candidates, stripped)
    for match in re.finditer(r"```(?:json)?\s*(.*?)```", stripped, flags=re.DOTALL | re.IGNORECASE):
        _append_unique(candidates, match.group(1).strip())

    object_start = stripped.find("{")
    object_end = stripped.rfind("}")
    if 0 <= object_start < object_end:
        _append_unique(candidates, stripped[object_start : object_end + 1])

    array_start = stripped.find("[")
    array_end = stripped.rfind("]")
    if 0 <= array_start < array_end:
        _append_unique(candidates, stripped[array_start : array_end + 1])

    return candidates


def _json_variants(text: str) -> list[str]:
    variants: list[str] = []
    _append_unique(variants, text.strip())
    repaired = _repair_json_like(text)
    _append_unique(variants, repaired)
    return variants


def _repair_json_like(text: str) -> str:
    repaired = text.strip()
    repaired = re.sub(r",(\s*[}\]])", r"\1", repaired)
    return re.sub(
        r'(?<=[}\]"])([ \t\r\n]+)(?=(?:\{|\[|"(?:[^"\\]|\\.)*"(?:\s*:)?))',
        r",\1",
        repaired,
    )


def _append_unique(items: list[str], value: str) -> None:
    if value and value not in items:
        items.append(value)
