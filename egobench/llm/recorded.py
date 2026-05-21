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
        tasks = _extract_block(prompt, "TASKS") or task
        tasks_json = _extract_json_block(prompt, "TASKS_JSON")
        labels_json = _extract_json_block(prompt, "LABELS_JSON")
        if "task_batch_annotations_json" in lower:
            entries = tasks_json if isinstance(tasks_json, list) else []
            joined = " ".join(str(entry.get("task") or "") for entry in entries if isinstance(entry, dict))
            group_label = _family_label(joined or prompt)
            annotations = []
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                text = str(entry.get("task") or "")
                annotations.append(
                    {
                        "conversation_id": str(entry.get("conversation_id") or ""),
                        "task_family": _family_label(text),
                        "domain": _domain(text),
                        "skills": _skills(text),
                        "family_fit": "strong",
                        "difficulty": _difficulty(text),
                        "specificity": _specificity(text),
                    }
                )
            return {
                "group": {
                    "task_family": group_label,
                    "domain": _domain(joined),
                    "skills": _skills(joined),
                    "group_summary": f"User asks for {group_label.lower()} tasks.",
                },
                "annotations": annotations,
            }
        if "canonical_family_map_json" in lower:
            entries = labels_json if isinstance(labels_json, list) else []
            families = []
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                label = str(entry.get("label") or "")
                if not label:
                    continue
                domains = entry.get("domains") if isinstance(entry.get("domains"), list) else []
                skills = entry.get("skills") if isinstance(entry.get("skills"), list) else []
                families.append(
                    {
                        "source_labels": [label],
                        "task_family": label,
                        "domain": str(domains[0]) if domains else _domain(label),
                        "skills": [str(skill) for skill in skills[:5]] or _skills(label),
                    }
                )
            return {"families": families}
        if "merge_checklist_batch_json" in lower:
            entries = tasks_json if isinstance(tasks_json, list) else []
            return {"checklists": [_batch_merged_checklist(entry) for entry in entries if isinstance(entry, dict)]}
        if "checklist_batch_json" in lower:
            entries = tasks_json if isinstance(tasks_json, list) else []
            return {
                "checklists": [
                    {
                        "conversation_id": str(entry.get("conversation_id") or ""),
                        "items": _checklist(str(entry.get("task") or "")),
                    }
                    for entry in entries
                    if isinstance(entry, dict)
                ]
            }
        if "task_family_group_json" in lower:
            label = _family_label(tasks)
            return {
                "task_family": label,
                "domain": _domain(tasks),
                "skills": _skills(tasks),
                "group_summary": f"User asks for {label.lower()} tasks.",
            }
        if "task_annotation_json" in lower:
            label = _family_label(task)
            return {
                "task_family": label,
                "domain": _domain(task),
                "skills": _skills(task),
                "family_fit": "strong",
                "difficulty": _difficulty(task),
                "specificity": _specificity(task),
            }
        if "canonical_task_family_json" in lower:
            label = _family_label(prompt)
            return {
                "task_family": label,
                "domain": _domain(prompt),
                "skills": _skills(prompt),
            }
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


def _extract_json_block(text: str, name: str):
    raw = _extract_block(text, name)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


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


def _family_label(text: str) -> str:
    label = _label(text)
    lower = text.lower()
    if "translate" in lower or "french" in lower or "spanish" in lower:
        return f"{label} language support"
    if "code" in lower or "python" in lower or "javascript" in lower or "api" in lower:
        return f"{label} software help"
    if "email" in lower or "rewrite" in lower or "draft" in lower:
        return f"{label} writing help"
    return f"{label} tasks" if label != "General" else "General assistance"


def _domain(text: str) -> str:
    lower = text.lower()
    if any(term in lower for term in ("translate", "french", "spanish", "grammar")):
        return "Language learning"
    if any(term in lower for term in ("code", "python", "javascript", "api", "sql")):
        return "Software engineering"
    if any(term in lower for term in ("email", "rewrite", "draft", "essay")):
        return "Writing"
    return "General"


def _skills(text: str) -> list[str]:
    label = _label(text).lower()
    return [f"{label} reasoning", "instruction following", "clear explanation"]


def _difficulty(text: str) -> str:
    words = len(text.split())
    if words < 8:
        return "easy"
    if words > 60 or text.count("\n") >= 2:
        return "hard"
    return "medium"


def _specificity(text: str) -> str:
    lower = text.lower()
    if any(marker in lower for marker in ("my ", "our ", "attached", "this file", "the following")):
        return "narrow"
    if len(text.split()) < 5:
        return "one_off"
    return "generalizable"


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


def _batch_merged_checklist(entry: dict) -> dict:
    task = str(entry.get("task") or "")
    panel_items = entry.get("panel_items", {})
    flat: list[str] = []
    if isinstance(panel_items, dict):
        for items in panel_items.values():
            if isinstance(items, list):
                flat.extend(str(item) for item in items)
    merged = _dedupe_items(flat) or _checklist(task)
    return {"conversation_id": str(entry.get("conversation_id") or ""), "items": merged[:10]}


def _dedupe_items(items: list[str]) -> list[str]:
    seen: set[str] = set()
    merged: list[str] = []
    for item in items:
        normalized = " ".join(str(item).lower().split())
        if normalized and normalized not in seen:
            seen.add(normalized)
            merged.append(str(item))
    return merged
