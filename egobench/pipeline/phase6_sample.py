from __future__ import annotations

import math
import random
from collections import defaultdict

from egobench.config import EgoBenchConfig
from egobench.db import DB


def run(db: DB, cfg: EgoBenchConfig) -> dict:
    rows = _rows(db)
    target = min(cfg.sample.target_n, len(rows))
    selected = _select(rows, target, cfg)
    selected_ids = {row["conversation_id"] for row in selected}
    with db.connect() as conn:
        conn.executemany(
            """
            UPDATE task_candidates
            SET selected = ?, updated_at = CURRENT_TIMESTAMP
            WHERE conversation_id = ?
            """,
            [(1 if row["conversation_id"] in selected_ids else 0, row["conversation_id"]) for row in rows],
        )
    return {"phase": 6, "selected": len(selected_ids), "target": target}


def _rows(db: DB) -> list[dict]:
    with db.connect() as conn:
        return [
            dict(row)
            for row in conn.execute(
                """
                SELECT conversation_id, category_label, importance
                FROM task_candidates
                WHERE is_task = 1
                ORDER BY conversation_id
                """
            )
        ]


def _select(rows: list[dict], target: int, cfg: EgoBenchConfig) -> list[dict]:
    if len(rows) <= target:
        return rows
    rng = random.Random(cfg.workspace.seed)
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        groups[row["category_label"] or "General"].append(row)
    weights = {
        label: len(group) ** cfg.sample.oversample_alpha
        for label, group in groups.items()
    }
    total_weight = sum(weights.values()) or 1.0
    quotas = {
        label: max(1, math.floor(target * weight / total_weight))
        for label, weight in weights.items()
    }
    while sum(quotas.values()) > target:
        label = max(quotas, key=lambda item: (quotas[item], len(groups[item])))
        quotas[label] -= 1
    while sum(quotas.values()) < target:
        label = max(weights, key=lambda item: weights[item] - quotas.get(item, 0))
        quotas[label] += 1

    selected: list[dict] = []
    for label, group in groups.items():
        ranked = sorted(
            group,
            key=lambda row: (
                rng.random() / max(0.05, float(row.get("importance") or 0.0)),
                row["conversation_id"],
            ),
        )
        selected.extend(ranked[: min(len(ranked), quotas.get(label, 0))])
    if len(selected) < target:
        already = {row["conversation_id"] for row in selected}
        remainder = [row for row in rows if row["conversation_id"] not in already]
        selected.extend(remainder[: target - len(selected)])
    return sorted(selected[:target], key=lambda row: row["conversation_id"])

