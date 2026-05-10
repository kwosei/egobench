from __future__ import annotations

import math

from egobench.db import DB


def run(db: DB) -> dict:
    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT conversation_id, cluster_size
            FROM task_candidates
            WHERE is_task = 1
            ORDER BY conversation_id
            """
        ).fetchall()
        values = [math.log1p(max(1, int(row["cluster_size"] or 1))) for row in rows]
        max_value = max(values, default=1.0) or 1.0
        updates = [
            (round(value / max_value, 6), rows[idx]["conversation_id"])
            for idx, value in enumerate(values)
        ]
        conn.executemany(
            """
            UPDATE task_candidates
            SET importance = ?, updated_at = CURRENT_TIMESTAMP
            WHERE conversation_id = ?
            """,
            updates,
        )
    return {"phase": 5, "scored": len(updates)}

