from __future__ import annotations

import json
import re

from egobench.config import EgoBenchConfig
from egobench.db import DB
from egobench.llm.factory import make_client
from egobench.llm.recorded import _checklist


def run(db: DB, cfg: EgoBenchConfig) -> dict:
    rows = _rows(db)
    updates: list[tuple[str, str, str]] = []
    for row in rows:
        raw: dict[str, list[str]] = {}
        for model in cfg.judges.checklist_panel:
            client = make_client(model, cfg, db, "phase7")
            prompt = (
                "Return CHECKLIST_JSON with key items. Include 5 to 10 concise rubric items.\n"
                f"<TASK>\n{row['first_user_text']}\n</TASK>"
            )
            raw[model] = _items_from_completion(client.complete(prompt).text, row["first_user_text"])
        merged = _merge(raw, row["first_user_text"], cfg, db)
        updates.append(
            (
                json.dumps(merged, sort_keys=True),
                json.dumps(raw, sort_keys=True),
                row["conversation_id"],
            )
        )
    with db.connect() as conn:
        conn.executemany(
            """
            UPDATE task_candidates
            SET checklist_json = ?, raw_checklists_json = ?, updated_at = CURRENT_TIMESTAMP
            WHERE conversation_id = ?
            """,
            updates,
        )
    return {"phase": 7, "checklists": len(updates)}


def _rows(db: DB) -> list[dict]:
    with db.connect() as conn:
        return [
            dict(row)
            for row in conn.execute(
                """
                SELECT conversation_id, first_user_text
                FROM task_candidates
                WHERE is_task = 1 AND selected = 1
                ORDER BY conversation_id
                """
            )
        ]


def _items_from_completion(text: str, task: str) -> list[str]:
    try:
        payload = _json_object(text)
        raw_items = payload.get("items", [])
        items = [str(item).strip() for item in raw_items if str(item).strip()]
        if items:
            return items[:10]
    except Exception:
        pass
    return _checklist(task)


def _merge(raw: dict[str, list[str]], task: str, cfg: EgoBenchConfig, db: DB) -> list[str]:
    flat = [item for items in raw.values() for item in items]
    client = make_client(cfg.judges.default, cfg, db, "phase7")
    prompt = (
        "Return MERGE_CHECKLIST_JSON with key items. Deduplicate and keep 5 to 10 items.\n"
        f"<TASK>\n{task}\n</TASK>\n"
        f"{json.dumps(flat, ensure_ascii=False)}"
    )
    merged = _items_from_completion(client.complete(prompt).text, task)
    deduped: list[str] = []
    seen: set[str] = set()
    for item in merged:
        normalized = " ".join(item.lower().split())
        if normalized not in seen:
            seen.add(normalized)
            deduped.append(item)
    return deduped[:10] or _checklist(task)


def _json_object(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))

