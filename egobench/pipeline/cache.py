from __future__ import annotations

import json
from typing import Any

from egobench.config import EgoBenchConfig, stable_config_dict
from egobench.db import DB
from egobench.pipeline.schema import stable_hash


def make_cache_key(phase: str, input_payload: Any, cfg: EgoBenchConfig) -> str:
    return stable_hash(
        {
            "phase": phase,
            "input": input_payload,
            "config": stable_config_dict(cfg),
            "seed": cfg.workspace.seed,
        }
    )


def read_cache(db: DB, phase: str, cache_key: str) -> dict[str, Any] | None:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT output_json FROM phase_cache WHERE phase = ? AND cache_key = ?",
            (phase, cache_key),
        ).fetchone()
        if row is None:
            return None
        return json.loads(row["output_json"])


def write_cache(db: DB, phase: str, cache_key: str, output: dict[str, Any]) -> None:
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO phase_cache(phase, cache_key, output_json, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(phase) DO UPDATE SET
              cache_key = excluded.cache_key,
              output_json = excluded.output_json,
              updated_at = CURRENT_TIMESTAMP
            """,
            (phase, cache_key, json.dumps(output, sort_keys=True)),
        )


def state_rows(db: DB) -> list[dict[str, Any]]:
    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT conversation_id, is_task, first_user_text, cluster_id, cluster_size,
                   category_label, category_description, importance, selected,
                   checklist_json, raw_checklists_json
            FROM task_candidates
            ORDER BY conversation_id
            """
        ).fetchall()
        return [dict(row) for row in rows]

