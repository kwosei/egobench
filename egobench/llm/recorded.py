from __future__ import annotations

import json
import re

from egobench.config import ModelRef
from egobench.llm.base import Completion, Usage, estimate_tokens


class RecordedLLMClient:
    """Deterministic local client used for tests and no-key smoke runs."""

    def __init__(self, ref: ModelRef | None = None, *, model: str | None = None):
        if ref is not None:
            self.ref = ref
            self.model = ref.model
        else:
            self.model = model or "local-recorded"
            self.ref = ModelRef(provider="recorded", model=self.model)

    def complete(self, prompt: str, *, temperature: float = 0.0) -> Completion:
        payload = self._payload(prompt)
        text = json.dumps(payload, sort_keys=True)
        return Completion(
            text=text,
            model=self.model,
            usage=Usage(input_tokens=estimate_tokens(prompt), output_tokens=estimate_tokens(text)),
            billable=False,
        )

    def _payload(self, prompt: str) -> dict:
        lower = prompt.lower()
        task = _extract_block(prompt, "TASK") or prompt
        if "category_json" in lower:
            label = _label(task)
            return {"label": label, "description": f"Tasks related to {label.lower()}."}
        if "merge_checklist_json" in lower:
            items = _merge_items(prompt)
            return {"items": items[:10] or _checklist(task)}
        if "checklist_json" in lower:
            return {"items": _checklist(task)}
        if "judge_score_json" in lower:
            response = _extract_block(prompt, "RESPONSE") or ""
            score = min(10, max(1, 5 + min(4, len(response.split()) // 35)))
            if "i don't know" in response.lower() or "cannot" in response.lower():
                score = max(3, score - 2)
            return {
                "score": score,
                "strengths": ["Addresses the main request"],
                "weaknesses": [] if score >= 8 else ["Could be more complete"],
                "rationale": "Deterministic local judging based on response coverage.",
            }
        if "candidate_response_json" in lower:
            first = " ".join(task.split()[:80])
            if "weak" in self.model:
                return {"response": "I don't know."}
            if "brief" in self.model:
                return {"response": f"Short answer: {first}"}
            if "detailed" in self.model:
                detail = (
                    "I would break this into context, constraints, concrete steps, risks, "
                    "and a quick verification pass. "
                )
                return {"response": f"Here is a detailed answer to your request: {first}\n\n{detail * 4}"}
            return {"response": f"Here is a focused answer to your request: {first}"}
        return {"text": "ok"}


def _extract_block(text: str, name: str) -> str:
    pattern = rf"<{name}>\n?(.*?)\n?</{name}>"
    match = re.search(pattern, text, flags=re.DOTALL)
    return match.group(1).strip() if match else ""


def _label(text: str) -> str:
    words = [word.lower() for word in re.findall(r"[a-zA-Z][a-zA-Z0-9_+-]{2,}", text)]
    stop = {
        "the",
        "and",
        "for",
        "with",
        "this",
        "that",
        "you",
        "can",
        "please",
        "help",
        "need",
        "want",
        "about",
    }
    for word in words:
        if word not in stop:
            return word.replace("_", " ").title()
    return "General"


def _checklist(text: str) -> list[str]:
    label = _label(text).lower()
    return [
        "Directly addresses the user's stated goal",
        "Uses the relevant details from the prompt",
        "Gives a concrete and actionable answer",
        "Avoids unsupported claims or invented facts",
        f"Covers the main {label} considerations",
    ]


def _merge_items(prompt: str) -> list[str]:
    try:
        start = prompt.index("[")
        end = prompt.rindex("]") + 1
        raw = json.loads(prompt[start:end])
    except Exception:
        return []
    seen: set[str] = set()
    merged: list[str] = []
    for item in raw:
        normalized = " ".join(str(item).lower().split())
        if normalized and normalized not in seen:
            seen.add(normalized)
            merged.append(str(item))
    return merged
