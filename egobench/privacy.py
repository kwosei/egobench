from __future__ import annotations

import json
import numbers
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol

from egobench.config import PrivacyCfg
from egobench.ingest.base import Conversation, Turn


@dataclass(frozen=True)
class RedactionSpan:
    start: int
    end: int
    label: str
    score: float | None = None


@dataclass(frozen=True)
class RedactionResult:
    text: str
    spans: list[RedactionSpan]


class PrivacyRedactor(Protocol):
    def redact(self, text: str) -> RedactionResult:
        raise NotImplementedError


class RegexPrivacyRedactor:
    def __init__(self, *, replacement: str = "[{label}]") -> None:
        self.replacement = replacement

    def redact(self, text: str) -> RedactionResult:
        spans: list[RedactionSpan] = []
        for label, pattern in _REGEX_PATTERNS:
            for match in pattern.finditer(text):
                spans.append(RedactionSpan(match.start(), match.end(), label))
        normalized = _normalize_spans(text, spans)
        return RedactionResult(
            text=apply_redactions(text, normalized, replacement=self.replacement),
            spans=normalized,
        )


class TransformersPrivacyRedactor:
    def __init__(
        self,
        *,
        model: str,
        score_threshold: float,
        replacement: str,
    ) -> None:
        try:
            from transformers import pipeline
        except ImportError as exc:
            raise RuntimeError(
                "Privacy redaction backend 'transformers' requires the optional "
                "Transformers runtime. Install it with `uv sync --extra privacy` "
                "or set [privacy].backend to 'endpoint' or 'regex'."
            ) from exc

        self.classifier = pipeline("token-classification", model=model)
        self.score_threshold = score_threshold
        self.replacement = replacement

    def redact(self, text: str) -> RedactionResult:
        raw_spans = self.classifier(text, aggregation_strategy="simple")
        spans = _spans_from_model_output(text, raw_spans, self.score_threshold)
        return RedactionResult(
            text=apply_redactions(text, spans, replacement=self.replacement),
            spans=spans,
        )


class EndpointPrivacyRedactor:
    def __init__(
        self,
        *,
        endpoint_url: str,
        score_threshold: float,
        replacement: str,
        timeout_s: float,
    ) -> None:
        self.endpoint_url = endpoint_url
        self.score_threshold = score_threshold
        self.replacement = replacement
        self.timeout_s = timeout_s

    def redact(self, text: str) -> RedactionResult:
        body = json.dumps({"text": text}).encode("utf-8")
        request = urllib.request.Request(
            self.endpoint_url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Privacy redaction endpoint failed: {exc}") from exc

        if isinstance(payload, dict) and isinstance(payload.get("error"), str):
            raise RuntimeError(f"Privacy redaction endpoint failed: {payload['error']}")

        spans = _spans_from_endpoint_payload(text, payload, self.score_threshold)
        if isinstance(payload, dict):
            redacted_text = payload.get("redacted_text") or payload.get("text")
            if isinstance(redacted_text, str):
                return RedactionResult(text=redacted_text, spans=spans)
        return RedactionResult(
            text=apply_redactions(text, spans, replacement=self.replacement),
            spans=spans,
        )


def make_redactor(cfg: PrivacyCfg) -> PrivacyRedactor:
    if cfg.backend == "transformers":
        return TransformersPrivacyRedactor(
            model=cfg.model,
            score_threshold=cfg.score_threshold,
            replacement=cfg.replacement,
        )
    if cfg.backend == "endpoint":
        if not cfg.endpoint_url:
            raise RuntimeError("[privacy].endpoint_url is required when backend = 'endpoint'.")
        return EndpointPrivacyRedactor(
            endpoint_url=cfg.endpoint_url,
            score_threshold=cfg.score_threshold,
            replacement=cfg.replacement,
            timeout_s=cfg.timeout_s,
        )
    if cfg.backend == "regex":
        return RegexPrivacyRedactor(replacement=cfg.replacement)
    raise RuntimeError(
        f"Unsupported [privacy].backend '{cfg.backend}'. "
        "Use 'transformers', 'endpoint', or 'regex'."
    )


def redact_conversations(
    conversations: list[Conversation],
    redactor: PrivacyRedactor,
) -> tuple[list[Conversation], int]:
    redacted: list[Conversation] = []
    total_spans = 0
    for conv in conversations:
        turns: list[Turn] = []
        conv_spans = 0
        for turn in conv.turns:
            result = redactor.redact(turn.text)
            conv_spans += len(result.spans)
            turns.append(Turn(role=turn.role, text=result.text, ts=turn.ts))
        metadata, metadata_spans = _redact_metadata(conv.metadata, redactor)
        conv_spans += metadata_spans
        total_spans += conv_spans
        redacted.append(
            Conversation(
                id=conv.id,
                turns=turns,
                model_used=conv.model_used,
                metadata={
                    **metadata,
                    "privacy_redacted": True,
                    "privacy_redaction_count": conv_spans,
                },
            )
        )
    return redacted, total_spans


def apply_redactions(
    text: str,
    spans: list[RedactionSpan],
    *,
    replacement: str,
) -> str:
    pieces: list[str] = []
    cursor = 0
    for span in _normalize_spans(text, spans):
        pieces.append(text[cursor : span.start])
        pieces.append(_replacement_text(replacement, span.label))
        cursor = span.end
    pieces.append(text[cursor:])
    return "".join(pieces)


def _redact_metadata(
    metadata: dict[str, Any],
    redactor: PrivacyRedactor,
) -> tuple[dict[str, Any], int]:
    count = 0

    def _redact(value: Any) -> Any:
        nonlocal count
        if isinstance(value, str):
            result = redactor.redact(value)
            count += len(result.spans)
            return result.text
        if isinstance(value, list):
            return [_redact(item) for item in value]
        if isinstance(value, dict):
            return {str(key): _redact(item) for key, item in value.items()}
        return value

    return {str(key): _redact(value) for key, value in metadata.items()}, count


def _replacement_text(template: str, label: str) -> str:
    return template.replace("{label}", label)


def _normalize_spans(text: str, spans: list[RedactionSpan]) -> list[RedactionSpan]:
    normalized: list[RedactionSpan] = []
    for span in sorted(spans, key=lambda item: (item.start, -(item.end - item.start))):
        start = max(0, min(len(text), int(span.start)))
        end = max(0, min(len(text), int(span.end)))
        if end <= start:
            continue
        candidate = RedactionSpan(start, end, _clean_label(span.label), span.score)
        if normalized and candidate.start < normalized[-1].end:
            previous = normalized[-1]
            if candidate.end > previous.end:
                normalized[-1] = RedactionSpan(
                    previous.start,
                    candidate.end,
                    previous.label,
                    _max_score(previous.score, candidate.score),
                )
            continue
        normalized.append(candidate)
    return normalized


def _spans_from_model_output(
    text: str,
    output: Any,
    score_threshold: float,
) -> list[RedactionSpan]:
    if not isinstance(output, list):
        return []
    spans: list[RedactionSpan] = []
    for item in output:
        span = _span_from_mapping(text, item, score_threshold)
        if span is not None:
            spans.append(span)
    return _normalize_spans(text, spans)


def _spans_from_endpoint_payload(
    text: str,
    payload: Any,
    score_threshold: float,
) -> list[RedactionSpan]:
    if isinstance(payload, list):
        return _spans_from_model_output(text, payload, score_threshold)
    if not isinstance(payload, dict):
        return []
    for key in ("spans", "redactions", "entities"):
        value = payload.get(key)
        if isinstance(value, list):
            return _spans_from_model_output(text, value, score_threshold)
    return []


def _span_from_mapping(
    text: str,
    item: Any,
    score_threshold: float,
) -> RedactionSpan | None:
    if not isinstance(item, dict):
        return None
    score = _coerce_float(item.get("score"))
    if score is not None and score < score_threshold:
        return None
    label = (
        item.get("entity_group")
        or item.get("entity")
        or item.get("label")
        or item.get("type")
        or "private"
    )
    start = _coerce_int(item.get("start"))
    end = _coerce_int(item.get("end"))
    if start is None or end is None:
        word = item.get("word") or item.get("text")
        if not isinstance(word, str) or not word:
            return None
        found_at = text.find(word)
        if found_at < 0:
            found_at = text.find(word.strip())
            word = word.strip()
        if found_at < 0:
            return None
        start = found_at
        end = found_at + len(word)
    return RedactionSpan(start=start, end=end, label=str(label), score=score)


def _clean_label(label: str) -> str:
    cleaned = label.strip()
    if len(cleaned) > 2 and cleaned[1] == "-":
        cleaned = cleaned[2:]
    return cleaned.lower().replace(" ", "_")


def _max_score(left: float | None, right: float | None) -> float | None:
    if left is None:
        return right
    if right is None:
        return left
    return max(left, right)


def _coerce_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, numbers.Integral):
        return int(value)
    if isinstance(value, numbers.Real):
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value)
    return None


def _coerce_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, numbers.Real):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


_REGEX_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "private_email",
        re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I),
    ),
    (
        "private_phone",
        re.compile(r"(?<!\w)(?:\+?1[\s.-]?)?(?:\(?\d{3}\)?[\s.-]?)\d{3}[\s.-]?\d{4}(?!\w)"),
    ),
    (
        "private_url",
        re.compile(r"\bhttps?://[^\s<>()]+", re.I),
    ),
    (
        "account_number",
        re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)"),
    ),
    (
        "private_address",
        re.compile(
            r"\b\d{1,6}\s+(?:[A-Z0-9.'-]+\s+){1,6}"
            r"(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Drive|Dr|Lane|Ln|Court|Ct|Way|Place|Pl)\b",
            re.I,
        ),
    ),
    (
        "secret",
        re.compile(
            r"\b(?:sk-[A-Za-z0-9_-]{20,}|sk-ant-[A-Za-z0-9_-]{20,}|"
            r"xox[baprs]-[A-Za-z0-9-]{20,}|ghp_[A-Za-z0-9_]{20,}|"
            r"[A-Za-z0-9_]*(?:TOKEN|SECRET|KEY|PASSWORD)[A-Za-z0-9_]*\s*=\s*['\"]?[^'\"\s]{8,})",
            re.I,
        ),
    ),
)
