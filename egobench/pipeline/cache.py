from __future__ import annotations

import json
from typing import Any, Sequence

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


TASK_CANDIDATE_COLUMNS = {
    "conversation_id",
    "is_task",
    "first_user_text",
    "cluster_id",
    "cluster_size",
    "near_duplicate_group_id",
    "near_duplicate_group_size",
    "candidate_group_id",
    "candidate_group_size",
    "category_label",
    "category_description",
    "task_family_id",
    "task_family",
    "domain",
    "skills_json",
    "family_fit",
    "difficulty",
    "specificity",
    "family_size",
    "family_importance",
    "importance",
    "selected",
    "checklist_json",
    "raw_checklists_json",
}


def state_rows(db: DB, columns: Sequence[str] | None = None) -> list[dict[str, Any]]:
    selected_columns = list(columns or TASK_CANDIDATE_COLUMNS)
    invalid = sorted(set(selected_columns) - TASK_CANDIDATE_COLUMNS)
    if invalid:
        raise ValueError(f"Unknown task candidate columns: {', '.join(invalid)}")
    select_clause = ", ".join(selected_columns)
    with db.connect() as conn:
        rows = conn.execute(
            f"""
            SELECT {select_clause}
            FROM task_candidates
            ORDER BY conversation_id
            """
        ).fetchall()
        return [dict(row) for row in rows]
