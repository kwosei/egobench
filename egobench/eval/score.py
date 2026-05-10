from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass


@dataclass(frozen=True)
class ScoreSummary:
    raw: float
    frequency_weighted: float
    per_category: dict[str, float]


def compute_scores(rows: list[dict]) -> ScoreSummary:
    if not rows:
        return ScoreSummary(raw=0.0, frequency_weighted=0.0, per_category={})
    raw = sum(float(row["score"]) for row in rows) / len(rows)
    total_weight = sum(max(1, int(row.get("cluster_size") or 1)) for row in rows) or 1
    frequency_weighted = (
        sum(float(row["score"]) * max(1, int(row.get("cluster_size") or 1)) for row in rows) / total_weight
    )
    by_category: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        by_category[str(row.get("category") or "General")].append(float(row["score"]))
    return ScoreSummary(
        raw=round(raw, 4),
        frequency_weighted=round(frequency_weighted, 4),
        per_category={
            category: round(sum(values) / len(values), 4)
            for category, values in sorted(by_category.items())
        },
    )

