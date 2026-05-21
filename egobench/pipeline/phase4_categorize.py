from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Iterable

from rich.console import Console

from egobench.config import EgoBenchConfig
from egobench.db import DB
from egobench.llm.factory import make_client
from egobench.llm.recorded import _label
from egobench.pipeline.json_utils import parse_json_object as _json_object


ANNOTATION_BATCH_SIZE = 8
CANONICAL_BATCH_SIZE = 120
MAX_WORKERS = 16

FAMILY_FIT = {"strong", "weak"}
DIFFICULTY = {"easy", "medium", "hard"}
SPECIFICITY = {"generalizable", "narrow", "one_off"}


def run(db: DB, cfg: EgoBenchConfig, console: Console | None = None) -> dict:
    console = console or Console()
    groups = _candidate_groups(db)
    judge = cfg.judges.default
    client = make_client(judge, cfg, db, "phase4")
    task_count = sum(len(rows) for rows in groups.values())
    console.print(
        f"[dim]phase4: inferring task families for {task_count} tasks "
        f"across {len(groups)} candidate groups with {judge.display()}[/dim]"
    )

    all_batches: list[list[dict]] = [
        batch
        for rows in groups.values()
        for batch in _chunks(rows, ANNOTATION_BATCH_SIZE)
    ]
    annotation_batches = len(all_batches)
    annotations: list[dict[str, Any]] = []
    fallback_count = 0
    done = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [executor.submit(_annotate_task_batch, client, batch) for batch in all_batches]
        for future in as_completed(futures):
            batch_annotations, batch_fallbacks = future.result()
            fallback_count += batch_fallbacks
            annotations.extend(batch_annotations)
            done += 1
            if done % max(1, annotation_batches // 5) == 0 or done == annotation_batches:
                console.print(f"[dim]phase4: annotated {done}/{annotation_batches} batches[/dim]")

    annotations = _canonicalize_annotations(client, annotations, console)
    _write_annotations(db, annotations)

    families = Counter(annotation["task_family_id"] for annotation in annotations)
    raw_family_count = len({annotation["raw_task_family"] for annotation in annotations})
    family_labels = {annotation["task_family_id"]: annotation["task_family"] for annotation in annotations}
    console.print(
        f"[dim]phase4: canonicalized {raw_family_count} raw family labels "
        f"into {len(families)} families[/dim]"
    )
    top_families = families.most_common(5)
    if top_families:
        top_text = ", ".join(f"{family_labels[family_id]} ({count})" for family_id, count in top_families)
        console.print(f"[dim]phase4: top task families: {top_text}[/dim]")
    return {
        "phase": 4,
        "families": len(families),
        "raw_families": raw_family_count,
        "tasks": len(annotations),
        "annotation_batches": annotation_batches,
        "canonical_batches": _canonical_batch_count(annotations),
        "fallbacks": fallback_count,
    }


def _candidate_groups(db: DB) -> dict[int, list[dict]]:
    grouped: dict[int, list[dict]] = defaultdict(list)
    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT conversation_id, first_user_text, candidate_group_id,
                   candidate_group_size, cluster_id, cluster_size
            FROM task_candidates
            WHERE is_task = 1
            ORDER BY candidate_group_id, conversation_id
            """
        ).fetchall()
    for row in rows:
        item = dict(row)
        group_id = item.get("candidate_group_id")
        if group_id is None:
            group_id = item.get("cluster_id")
        grouped[int(group_id or 0)].append(item)
    return dict(grouped)


def _annotate_task_batch(client: Any, rows: list[dict]) -> tuple[list[dict[str, Any]], int]:
    group_fallback = _fallback_group_meta(rows)
    prompt = (
        "Return TASK_BATCH_ANNOTATIONS_JSON only. Infer open-ended family metadata "
        "for this candidate group batch, then annotate every benchmark candidate.\n"
        "Output one JSON object with keys `group` and `annotations`.\n"
        "`group` keys: task_family, domain, skills, group_summary.\n"
        "`annotations` is a list with one object per input task. Required keys: "
        "conversation_id, task_family, domain, skills, family_fit, difficulty, specificity.\n"
        "Allowed values: family_fit strong|weak; difficulty easy|medium|hard; "
        "specificity generalizable|narrow|one_off.\n"
        "Use concise human-readable family labels and do not choose from a fixed taxonomy.\n"
        "<TASKS_JSON>\n"
        + json.dumps(_task_payload(rows), ensure_ascii=False, sort_keys=True)
        + "\n</TASKS_JSON>"
    )
    try:
        payload = _json_object(client.complete(prompt).text)
        group_meta = _group_meta_from_payload(payload.get("group"), rows, group_fallback)
        raw_annotations = payload.get("annotations", [])
        by_id = _annotations_by_id(raw_annotations)
    except Exception:
        return [_fallback_row_annotation(row, group_fallback) for row in rows], len(rows)

    annotations: list[dict[str, Any]] = []
    fallbacks = 0
    for row in rows:
        raw = by_id.get(str(row["conversation_id"]))
        if raw is None:
            annotations.append(_fallback_row_annotation(row, group_meta))
            fallbacks += 1
            continue
        try:
            annotation = _annotation_from_payload(raw, row["first_user_text"], group_meta)
        except Exception:
            annotation = _fallback_annotation(row["first_user_text"], group_meta)
            fallbacks += 1
        annotations.append(_row_annotation(row, annotation))
    return annotations, fallbacks


def _task_payload(rows: list[dict]) -> list[dict[str, str]]:
    return [
        {
            "conversation_id": str(row["conversation_id"]),
            "task": str(row.get("first_user_text") or ""),
        }
        for row in rows
    ]


def _group_meta_from_payload(raw: Any, rows: list[dict], fallback: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return fallback
    texts = [str(row.get("first_user_text") or "") for row in rows]
    return {
        "task_family": _clean_text(raw.get("task_family")) or fallback["task_family"],
        "domain": _clean_text(raw.get("domain")) or fallback["domain"],
        "skills": _skills(raw.get("skills"), texts) or fallback["skills"],
        "group_summary": _clean_text(raw.get("group_summary")) or "",
    }


def _annotations_by_id(raw: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, list):
        raise ValueError("annotations must be a list")
    out: dict[str, dict[str, Any]] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        conversation_id = _clean_text(item.get("conversation_id"))
        if conversation_id:
            out[conversation_id] = item
    return out


def _fallback_group_meta(rows: list[dict]) -> dict[str, Any]:
    texts = [str(row.get("first_user_text") or "") for row in rows]
    return {
        "task_family": _fallback_family(texts),
        "domain": _fallback_domain(texts),
        "skills": _skills(None, texts),
        "group_summary": "",
    }


def _fallback_row_annotation(row: dict, group_meta: dict[str, Any]) -> dict[str, Any]:
    return _row_annotation(
        row,
        _fallback_annotation(str(row.get("first_user_text") or ""), group_meta),
    )


def _row_annotation(row: dict, annotation: dict[str, Any]) -> dict[str, Any]:
    return {
        **annotation,
        "conversation_id": row["conversation_id"],
        "first_user_text": row["first_user_text"],
        "candidate_group_id": row.get("candidate_group_id"),
    }


def _canonicalize_annotations(
    client: Any,
    annotations: list[dict[str, Any]],
    console: Console,
) -> list[dict[str, Any]]:
    if not annotations:
        return []

    indexed = [
        {
            **annotation,
            "_index": idx,
            "raw_task_family": annotation.get("task_family") or "General assistance",
        }
        for idx, annotation in enumerate(annotations)
    ]
    by_label: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for annotation in indexed:
        by_label[str(annotation["raw_task_family"])].append(annotation)

    canonical_by_label = _canonical_family_map(client, by_label, console)
    canonicalized: list[dict[str, Any]] = []
    for annotation in indexed:
        raw_label = str(annotation["raw_task_family"])
        canonical = canonical_by_label.get(raw_label) or _fallback_canonical_family(by_label[raw_label])
        family_id = _family_id(canonical["task_family"], canonical["domain"])
        updated = dict(annotation)
        updated["task_family_id"] = family_id
        updated["task_family"] = canonical["task_family"]
        updated["domain"] = canonical["domain"] or updated.get("domain") or "General"
        if not updated.get("skills"):
            updated["skills"] = canonical["skills"]
        canonicalized.append(updated)

    canonicalized.sort(key=lambda annotation: int(annotation["_index"]))
    for annotation in canonicalized:
        annotation.pop("_index", None)
    return canonicalized


def _canonical_family_map(
    client: Any,
    by_label: dict[str, list[dict[str, Any]]],
    console: Console,
) -> dict[str, dict[str, Any]]:
    labels = list(by_label.keys())
    if not labels:
        return {}
    if len(labels) == 1:
        label = labels[0]
        return {label: _fallback_canonical_family(by_label[label])}

    console.print(
        f"[dim]phase4: canonicalizing {len(labels)} family labels "
        f"in batches of {CANONICAL_BATCH_SIZE}[/dim]"
    )
    canonical: dict[str, dict[str, Any]] = {}
    canonical_batches = list(_chunks(labels, CANONICAL_BATCH_SIZE))
    done = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [
            executor.submit(_canonical_family_map_once, client, batch, by_label)
            for batch in canonical_batches
        ]
        for future in as_completed(futures):
            canonical.update(future.result())
            done += 1
            console.print(f"[dim]phase4: canonical batch {done}/{len(canonical_batches)} done[/dim]")
    return canonical


def _canonical_family_map_once(
    client: Any,
    labels: list[str],
    by_label: dict[str, list[dict[str, Any]]],
) -> dict[str, dict[str, Any]]:
    prompt = (
        "Return CANONICAL_FAMILY_MAP_JSON only. Group genuinely synonymous task "
        "family labels and choose one concise canonical family per group.\n"
        "Output JSON object: {\"families\":[{\"source_labels\":[...],"
        "\"task_family\":\"...\",\"domain\":\"...\",\"skills\":[...]}]}.\n"
        "Every source label must appear exactly once, using the label strings verbatim. "
        "Do not merge labels that are merely related.\n"
        "<LABELS_JSON>\n"
        + json.dumps(_label_payload(labels, by_label), ensure_ascii=False, sort_keys=True)
        + "\n</LABELS_JSON>"
    )
    try:
        payload = _json_object(client.complete(prompt).text)
        families = payload.get("families", [])
        return _validate_canonical_families(families, labels, by_label)
    except Exception:
        return {label: _fallback_canonical_family(by_label[label]) for label in labels}


def _label_payload(labels: list[str], by_label: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for label in labels:
        annotations = by_label[label]
        domains = _top_values(annotation.get("domain") for annotation in annotations)
        skills = _top_values(skill for annotation in annotations for skill in annotation.get("skills", []))
        payload.append(
            {
                "label": label,
                "count": len(annotations),
                "domains": domains[:3],
                "skills": skills[:6],
            }
        )
    return payload


def _validate_canonical_families(
    raw: Any,
    labels: list[str],
    by_label: dict[str, list[dict[str, Any]]],
) -> dict[str, dict[str, Any]]:
    label_set = set(labels)
    seen: set[str] = set()
    out: dict[str, dict[str, Any]] = {}
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            sources = item.get("source_labels") or item.get("labels") or []
            if not isinstance(sources, list):
                continue
            valid_sources = [str(source) for source in sources if str(source) in label_set and str(source) not in seen]
            if not valid_sources:
                continue
            fallback_annotations = [annotation for label in valid_sources for annotation in by_label[label]]
            fallback = _fallback_canonical_family(fallback_annotations)
            canonical = {
                "task_family": _clean_text(item.get("task_family")) or fallback["task_family"],
                "domain": _clean_text(item.get("domain")) or fallback["domain"],
                "skills": _skills(item.get("skills"), [fallback["task_family"]]) or fallback["skills"],
            }
            for label in valid_sources:
                seen.add(label)
                out[label] = canonical
    for label in labels:
        if label not in seen:
            out[label] = _fallback_canonical_family(by_label[label])
    return out


def _write_annotations(db: DB, annotations: list[dict[str, Any]]) -> None:
    updates = [
        (
            annotation["task_family_id"],
            annotation["task_family"],
            annotation["domain"],
            json.dumps(annotation["skills"], sort_keys=True, ensure_ascii=False),
            annotation["family_fit"],
            annotation["difficulty"],
            annotation["specificity"],
            annotation["task_family"][:80],
            _category_description(annotation),
            annotation["conversation_id"],
        )
        for annotation in annotations
    ]
    with db.connect() as conn:
        conn.executemany(
            """
            UPDATE task_candidates
            SET task_family_id = ?,
                task_family = ?,
                domain = ?,
                skills_json = ?,
                family_fit = ?,
                difficulty = ?,
                specificity = ?,
                category_label = ?,
                category_description = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE conversation_id = ?
            """,
            updates,
        )


def _canonical_batch_count(annotations: list[dict[str, Any]]) -> int:
    labels = {annotation.get("raw_task_family") or annotation.get("task_family") for annotation in annotations}
    if len(labels) <= 1:
        return 0
    return (len(labels) + CANONICAL_BATCH_SIZE - 1) // CANONICAL_BATCH_SIZE


def _fallback_canonical_family(annotations: list[dict[str, Any]]) -> dict[str, Any]:
    label = _most_common_text(annotation.get("task_family") for annotation in annotations) or "General assistance"
    domain = _most_common_text(annotation.get("domain") for annotation in annotations) or "General"
    skills: list[str] = []
    seen: set[str] = set()
    for annotation in annotations:
        for skill in annotation.get("skills") or []:
            normalized = _normalized_family_text(skill)
            if normalized and normalized not in seen:
                seen.add(normalized)
                skills.append(str(skill))
    return {"task_family": label, "domain": domain, "skills": skills[:8] or ["instruction following"]}


def _most_common_text(values: Iterable[Any]) -> str:
    counts = Counter(_clean_text(value) for value in values if _clean_text(value))
    if not counts:
        return ""
    return max(counts, key=lambda value: (counts[value], len(_tokens(value)), value))


def _top_values(values: Iterable[Any]) -> list[str]:
    counts = Counter(_clean_text(value) for value in values if _clean_text(value))
    return [value for value, _ in counts.most_common()]


def _family_id(task_family: str, domain: str) -> str:
    payload = f"{_normalized_family_text(domain)}\n{_normalized_family_text(task_family)}"
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:8]
    slug = "-".join(_tokens(task_family)[:8]) or "general-assistance"
    return f"{slug}-{digest}"


def _normalized_family_text(value: Any) -> str:
    return " ".join(_tokens(str(value or "")))


def _tokens(text: str) -> list[str]:
    return [token.lower() for token in re.findall(r"[a-zA-Z][a-zA-Z0-9_+-]{1,}", text)]


def _annotation_from_payload(payload: dict[str, Any], text: str, group_meta: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("annotation payload must be an object")
    fallback = _fallback_annotation(text, group_meta)
    task_family = _clean_text(payload.get("task_family")) or fallback["task_family"]
    domain = _clean_text(payload.get("domain")) or fallback["domain"]
    return {
        "task_family": task_family,
        "domain": domain,
        "skills": _skills(payload.get("skills"), [text]) or fallback["skills"],
        "family_fit": _controlled(payload.get("family_fit"), FAMILY_FIT, fallback["family_fit"]),
        "difficulty": _controlled(payload.get("difficulty"), DIFFICULTY, fallback["difficulty"]),
        "specificity": _controlled(payload.get("specificity"), SPECIFICITY, fallback["specificity"]),
    }


def _fallback_annotation(text: str, group_meta: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_family": _clean_text(group_meta.get("task_family")) or _fallback_family([text]),
        "domain": _clean_text(group_meta.get("domain")) or _fallback_domain([text]),
        "skills": list(group_meta.get("skills") or _skills(None, [text])),
        "family_fit": "strong",
        "difficulty": _difficulty(text),
        "specificity": _specificity(text),
    }


def _fallback_family(texts: list[str]) -> str:
    label = _label(" ".join(texts))
    if label == "General":
        return "General assistance"
    joined = " ".join(texts).lower()
    if "translate" in joined or "french" in joined or "spanish" in joined:
        return f"{label} language support"
    if "code" in joined or "python" in joined or "javascript" in joined:
        return f"{label} software help"
    return f"{label} tasks"


def _fallback_domain(texts: list[str]) -> str:
    joined = " ".join(texts).lower()
    if any(term in joined for term in ("translate", "french", "spanish", "grammar")):
        return "Language learning"
    if any(term in joined for term in ("code", "python", "javascript", "sql", "api")):
        return "Software engineering"
    if any(term in joined for term in ("email", "memo", "essay", "rewrite", "draft")):
        return "Writing"
    return "General"


def _skills(raw: Any, texts: list[str]) -> list[str]:
    if isinstance(raw, list):
        skills = [_clean_text(item) for item in raw]
    elif isinstance(raw, str):
        skills = [_clean_text(item) for item in raw.split(",")]
    else:
        label = _label(" ".join(texts)).lower()
        skills = [f"{label} reasoning", "instruction following"]
    cleaned: list[str] = []
    seen: set[str] = set()
    for skill in skills:
        if not skill:
            continue
        normalized = " ".join(skill.lower().split())
        if normalized in seen:
            continue
        seen.add(normalized)
        cleaned.append(skill[:80])
    return cleaned[:8] or ["instruction following"]


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


def _controlled(value: Any, allowed: set[str], default: str) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in allowed else default


def _category_description(annotation: dict[str, Any]) -> str:
    skills = ", ".join(annotation["skills"][:4])
    parts = [annotation["domain"]]
    if skills:
        parts.append(f"skills: {skills}")
    return "; ".join(part for part in parts if part)[:500]


def _clean_text(value: Any) -> str:
    text = " ".join(str(value or "").split())
    return text[:200]


def _chunks(items: list[Any], size: int) -> Iterable[list[Any]]:
    for index in range(0, len(items), size):
        yield items[index : index + size]
