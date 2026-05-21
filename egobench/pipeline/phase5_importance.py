from __future__ import annotations

import math
from collections import Counter, defaultdict

from rich.console import Console

from egobench.db import DB


DIFFICULTY_WEIGHT = {"easy": 0.9, "medium": 1.05, "hard": 1.1}


def run(db: DB, console: Console | None = None) -> dict:
    console = console or Console()
    rows = _rows(db)
    family_rows: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        family_rows[_family_id(row)].append(row)

    raw_scores: dict[str, float] = {}
    for family, family_group in family_rows.items():
        size = len(family_group)
        duplicate_groups = {
            row.get("near_duplicate_group_id") if row.get("near_duplicate_group_id") is not None else row["conversation_id"]
            for row in family_group
        }
        diversity = len(duplicate_groups)
        difficulty_factor = sum(
            DIFFICULTY_WEIGHT.get(str(row.get("difficulty") or "medium"), 1.0)
            for row in family_group
        ) / max(1, size)
        frequency_score = math.log1p(size)
        diversity_score = math.log1p(diversity)
        raw_scores[family] = (
            (0.35 * frequency_score + 0.65 * diversity_score)
            * difficulty_factor
        )

    max_score = max(raw_scores.values(), default=1.0) or 1.0
    updates: list[tuple[int, float, float, str]] = []
    for row in rows:
        family = _family_id(row)
        normalized = round(raw_scores.get(family, 0.0) / max_score, 6)
        updates.append((len(family_rows[family]), normalized, normalized, row["conversation_id"]))

    with db.connect() as conn:
        conn.executemany(
            """
            UPDATE task_candidates
            SET family_size = ?,
                family_importance = ?,
                importance = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE conversation_id = ?
            """,
            updates,
        )
    top = Counter({family: len(group) for family, group in family_rows.items()}).most_common(5)
    if top:
        console.print(
            "[dim]phase5: largest families: "
            + ", ".join(f"{_family_label(family_rows[family])} ({count})" for family, count in top)
            + "[/dim]"
        )
    console.print(f"[dim]phase5: scored importance for {len(updates)} tasks in {len(family_rows)} families[/dim]")
    return {"phase": 5, "scored": len(updates), "families": len(family_rows)}


def _rows(db: DB) -> list[dict]:
    with db.connect() as conn:
        return [
            dict(row)
            for row in conn.execute(
                """
                SELECT conversation_id, task_family_id, task_family, near_duplicate_group_id,
                       difficulty, specificity
                FROM task_candidates
                WHERE is_task = 1
                ORDER BY conversation_id
                """
            )
        ]


def _family_id(row: dict) -> str:
    return str(row.get("task_family_id") or row.get("task_family") or "General assistance")


def _family_label(rows: list[dict]) -> str:
    counts = Counter(str(row.get("task_family") or "General assistance") for row in rows)
    return max(counts, key=lambda label: (counts[label], label))
