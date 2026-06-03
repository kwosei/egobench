from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Iterable

from rich.console import Console

from egobench.config import EgoBenchConfig, ModelRef
from egobench.db import DB
from egobench.llm.factory import make_client
from egobench.llm.pricing import PricingResolver
from egobench.llm.recorded import _checklist
from egobench.pipeline.json_utils import parse_json_object as _json_object


CHECKLIST_BATCH_SIZE = 5
MAX_WORKERS = 16


def run(
    db: DB,
    cfg: EgoBenchConfig,
    console: Console | None = None,
    *,
    pricing: PricingResolver | None = None,
) -> dict:
    console = console or Console()
    rows = _rows(db)
    panel = list(cfg.judges.checklist_panel)
    console.print(
        f"[dim]phase7: building checklists for {len(rows)} tasks "
        f"({len(panel)} panel models + batched merge, batches of {CHECKLIST_BATCH_SIZE})[/dim]"
    )

    raw_by_task: dict[str, dict[str, list[str]]] = {row["conversation_id"]: {} for row in rows}
    panel_work: list[tuple[Any, str, list[dict]]] = []
    for ref in panel:
        key = ref.display()
        client = make_client(ref, cfg, db, "phase7", pricing=pricing)
        for batch in _chunks(rows, CHECKLIST_BATCH_SIZE):
            panel_work.append((client, key, batch))
    panel_calls = len(panel_work)
    panel_done = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(_panel_batch, client, key, batch, console): key
            for client, key, batch in panel_work
        }
        for future in as_completed(futures):
            key = futures[future]
            for conversation_id, items in future.result().items():
                raw_by_task[conversation_id][key] = items
            panel_done += 1
            if panel_done % max(1, panel_calls // 5) == 0 or panel_done == panel_calls:
                console.print(f"[dim]phase7: panel {panel_done}/{panel_calls} batches done[/dim]")

    merged_by_task: dict[str, list[str]] = {}
    merge_client = make_client(cfg.judges.default, cfg, db, "phase7", pricing=pricing)
    merge_batches_list = list(_chunks(rows, CHECKLIST_BATCH_SIZE))
    merge_calls = len(merge_batches_list)
    merge_done = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures_merge = [
            executor.submit(_merge_batch, merge_client, cfg.judges.default, batch, raw_by_task, console)
            for batch in merge_batches_list
        ]
        for future in as_completed(futures_merge):
            merged_by_task.update(future.result())
            merge_done += 1
            if merge_done % max(1, merge_calls // 5) == 0 or merge_done == merge_calls:
                console.print(f"[dim]phase7: merge {merge_done}/{merge_calls} batches done[/dim]")

    updates = [
        (
            json.dumps(merged_by_task.get(row["conversation_id"]) or _checklist(row["first_user_text"]), sort_keys=True),
            json.dumps(raw_by_task.get(row["conversation_id"], {}), sort_keys=True),
            row["conversation_id"],
        )
        for row in rows
    ]
    with db.connect() as conn:
        conn.executemany(
            """
            UPDATE task_candidates
            SET checklist_json = ?, raw_checklists_json = ?, updated_at = CURRENT_TIMESTAMP
            WHERE conversation_id = ?
            """,
            updates,
        )
    return {
        "phase": 7,
        "checklists": len(updates),
        "panel_batches": panel_calls,
        "merge_batches": merge_calls,
    }


def _rows(db: DB) -> list[dict]:
    with db.connect() as conn:
        return [
            dict(row)
            for row in conn.execute(
                """
                SELECT conversation_id, first_user_text, task_family, domain,
                       skills_json, difficulty, specificity
                FROM task_candidates
                WHERE is_task = 1 AND selected = 1
                ORDER BY conversation_id
                """
            )
        ]


def _panel_batch(client: Any, key: str, rows: list[dict], console: Console) -> dict[str, list[str]]:
    prompt = (
        "Return CHECKLIST_BATCH_JSON only. For each task, draft 5 to 10 concise rubric items.\n"
        "Output JSON object: {\"checklists\":[{\"conversation_id\":\"...\",\"items\":[...]}]}.\n"
        "Every input conversation_id must appear exactly once.\n"
        "<TASKS_JSON>\n"
        + json.dumps(_task_payload(rows), ensure_ascii=False, sort_keys=True)
        + "\n</TASKS_JSON>"
    )
    try:
        payload = _json_object(client.complete(prompt).text)
        return _items_by_id(payload.get("checklists"), rows)
    except Exception as err:
        console.print(f"[dim]    panel {key} failed ({err}); using fallback checklist[/dim]")
        return {row["conversation_id"]: _checklist(row["first_user_text"]) for row in rows}


def _merge_batch(
    client: Any,
    ref: ModelRef,
    rows: list[dict],
    raw_by_task: dict[str, dict[str, list[str]]],
    console: Console,
) -> dict[str, list[str]]:
    prompt = (
        "Return MERGE_CHECKLIST_BATCH_JSON only. For each task, deduplicate panel rubric items "
        "and keep 5 to 10 final checklist items.\n"
        "Output JSON object: {\"checklists\":[{\"conversation_id\":\"...\",\"items\":[...]}]}.\n"
        "Every input conversation_id must appear exactly once.\n"
        "<TASKS_JSON>\n"
        + json.dumps(_merge_payload(rows, raw_by_task), ensure_ascii=False, sort_keys=True)
        + "\n</TASKS_JSON>"
    )
    try:
        payload = _json_object(client.complete(prompt).text)
        merged = _items_by_id(payload.get("checklists"), rows)
    except Exception as err:
        console.print(f"[dim]    merge {ref.display()} failed ({err}); using fallback checklist[/dim]")
        return {
            row["conversation_id"]: _fallback_merge(raw_by_task.get(row["conversation_id"], {}), row["first_user_text"])
            for row in rows
        }
    return {
        row["conversation_id"]: _dedupe_items(
            merged.get(row["conversation_id"])
            or _fallback_merge(raw_by_task.get(row["conversation_id"], {}), row["first_user_text"])
        )
        for row in rows
    }


def _task_payload(rows: list[dict]) -> list[dict[str, Any]]:
    return [
        {
            "conversation_id": row["conversation_id"],
            "metadata": _metadata_dict(row),
            "task": row["first_user_text"],
        }
        for row in rows
    ]


def _merge_payload(rows: list[dict], raw_by_task: dict[str, dict[str, list[str]]]) -> list[dict[str, Any]]:
    return [
        {
            "conversation_id": row["conversation_id"],
            "metadata": _metadata_dict(row),
            "task": row["first_user_text"],
            "panel_items": raw_by_task.get(row["conversation_id"], {}),
        }
        for row in rows
    ]


def _items_by_id(raw: Any, rows: list[dict]) -> dict[str, list[str]]:
    if not isinstance(raw, list):
        raise ValueError("checklists must be a list")
    expected = {row["conversation_id"]: row["first_user_text"] for row in rows}
    out: dict[str, list[str]] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        conversation_id = str(item.get("conversation_id") or "")
        if conversation_id not in expected:
            continue
        items = _clean_items(item.get("items"))
        if items:
            out[conversation_id] = items
    for conversation_id, task in expected.items():
        if conversation_id not in out:
            out[conversation_id] = _checklist(task)
    return out


def _clean_items(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()][:10]


def _fallback_merge(raw: dict[str, list[str]], task: str) -> list[str]:
    flat = [item for items in raw.values() for item in items]
    return _dedupe_items(flat) or _checklist(task)


def _dedupe_items(items: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for item in items:
        normalized = " ".join(str(item).lower().split())
        if normalized and normalized not in seen:
            seen.add(normalized)
            deduped.append(str(item))
    return deduped[:10]


def _metadata_dict(row: dict) -> dict[str, Any]:
    return {
        "task_family": row.get("task_family") or "General assistance",
        "domain": row.get("domain") or "General",
        "skills": _skills(row.get("skills_json")) or ["instruction following"],
        "difficulty": row.get("difficulty") or "medium",
        "specificity": row.get("specificity") or "generalizable",
    }


def _metadata_block(row: dict) -> str:
    metadata = _metadata_dict(row)
    return "\n".join(
        [
            f"Task family: {metadata['task_family']}",
            f"Domain: {metadata['domain']}",
            f"Skills: {', '.join(metadata['skills'])}",
            f"Difficulty: {metadata['difficulty']}",
            f"Specificity: {metadata['specificity']}",
        ]
    )


def _skills(raw: str | None) -> list[str]:
    try:
        parsed = json.loads(raw or "[]")
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if str(item).strip()]


def _chunks(items: list[Any], size: int) -> Iterable[list[Any]]:
    for index in range(0, len(items), size):
        yield items[index : index + size]
