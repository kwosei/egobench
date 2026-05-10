from __future__ import annotations

import json
from collections import defaultdict

from egobench.config import EgoBenchConfig
from egobench.db import DB
from egobench.llm.factory import make_client
from egobench.llm.recorded import _label


def run(db: DB, cfg: EgoBenchConfig) -> dict:
    clusters = _clusters(db)
    client = make_client(cfg.judges.default, cfg, db, "phase4")
    updates: list[tuple[str, str, int]] = []
    for cluster_id, texts in clusters.items():
        prompt = (
            "Return CATEGORY_JSON with keys label and description for these user tasks.\n"
            f"<TASK>\n{chr(10).join(texts[:5])}\n</TASK>"
        )
        try:
            payload = json.loads(client.complete(prompt).text)
            label = str(payload.get("label") or _label(" ".join(texts)))
            description = str(payload.get("description") or f"Tasks related to {label.lower()}.")
        except Exception:
            label = _label(" ".join(texts))
            description = f"Tasks related to {label.lower()}."
        updates.append((label[:80], description[:500], cluster_id))
    with db.connect() as conn:
        conn.executemany(
            """
            UPDATE task_candidates
            SET category_label = ?, category_description = ?, updated_at = CURRENT_TIMESTAMP
            WHERE cluster_id = ?
            """,
            updates,
        )
    return {"phase": 4, "categories": len(updates)}


def _clusters(db: DB) -> dict[int, list[str]]:
    out: dict[int, list[str]] = defaultdict(list)
    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT cluster_id, first_user_text
            FROM task_candidates
            WHERE is_task = 1
            ORDER BY cluster_id, conversation_id
            """
        ).fetchall()
    for row in rows:
        out[int(row["cluster_id"])].append(row["first_user_text"])
    return dict(out)

