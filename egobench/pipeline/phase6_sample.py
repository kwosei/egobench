from __future__ import annotations

import hashlib
import math
from collections import Counter, defaultdict

from rich.console import Console

from egobench.config import EgoBenchConfig
from egobench.db import DB


def run(db: DB, cfg: EgoBenchConfig, console: Console | None = None) -> dict:
    console = console or Console()
    rows = _rows(db)
    target = min(cfg.sample.target_n, len(rows))
    console.print(f"[dim]phase6: selecting {target} tasks from {len(rows)} annotated tasks[/dim]")
    selected, stats = _select(rows, target, cfg)
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
    family_counts = Counter(_family_id(row) for row in selected)
    top_selected = family_counts.most_common(5)
    if top_selected:
        labels = _family_labels(selected)
        console.print(
            "[dim]phase6: top selected families: "
            + ", ".join(f"{labels[family]} ({count})" for family, count in top_selected)
            + "[/dim]"
        )
    console.print(
        f"[dim]phase6: selected {len(selected_ids)} tasks across {len(family_counts)} families; "
        f"suppressed {stats['duplicate_groups_suppressed']} duplicate variants[/dim]"
    )
    return {
        "phase": 6,
        "selected": len(selected_ids),
        "target": target,
        "families": len(family_counts),
        **stats,
    }


def _rows(db: DB) -> list[dict]:
    with db.connect() as conn:
        return [
            dict(row)
            for row in conn.execute(
                """
                SELECT conversation_id, task_family_id, task_family, domain, skills_json,
                       difficulty, specificity, family_size,
                       family_importance, near_duplicate_group_id,
                       near_duplicate_group_size
                FROM task_candidates
                WHERE is_task = 1
                ORDER BY conversation_id
                """
            )
        ]


def _select(rows: list[dict], target: int, cfg: EgoBenchConfig) -> tuple[list[dict], dict[str, int]]:
    if target <= 0:
        return [], _stats(rows, [], 0)
    selected = _select_pass(rows, target, cfg)
    return selected[:target], _stats(rows, selected[:target], _duplicate_suppressed(rows))


def _select_pass(rows: list[dict], target: int, cfg: EgoBenchConfig) -> list[dict]:
    deduped = _best_by_duplicate_group(rows, cfg)
    family_rows: dict[str, list[dict]] = defaultdict(list)
    for row in deduped:
        family_rows[_family_id(row)].append(row)
    for family in family_rows:
        family_rows[family] = sorted(family_rows[family], key=lambda row: _rank(row, cfg))

    selected: list[dict] = []
    selected_ids: set[str] = set()
    family_counts: Counter[str] = Counter()
    duplicate_groups: set[object] = set()

    recurring = sorted(
        family_rows,
        key=lambda family: (
            -max(float(row.get("family_importance") or 0.0) for row in family_rows[family]),
            -max(int(row.get("family_size") or len(family_rows[family])) for row in family_rows[family]),
            family,
        ),
    )
    for family in recurring:
        if len(selected) >= target:
            break
        if max(int(row.get("family_size") or 1) for row in family_rows[family]) < 2:
            continue
        _try_add_family_candidate(family, family_rows, selected, selected_ids, family_counts, duplicate_groups, cfg)

    long_tail_target = _long_tail_target(target, cfg)
    long_tail_added = 0
    rare_families = sorted(
        family_rows,
        key=lambda family: (
            min(int(row.get("family_size") or 1) for row in family_rows[family]),
            -max(float(row.get("family_importance") or 0.0) for row in family_rows[family]),
            family,
        ),
    )
    for family in rare_families:
        if len(selected) >= target or long_tail_added >= long_tail_target:
            break
        if max(int(row.get("family_size") or 1) for row in family_rows[family]) > 2:
            continue
        before = len(selected)
        _try_add_family_candidate(family, family_rows, selected, selected_ids, family_counts, duplicate_groups, cfg)
        long_tail_added += int(len(selected) > before)

    candidates = sorted(
        deduped,
        key=lambda row: (
            family_counts[_family_id(row)],
            -float(row.get("family_importance") or 0.0),
            *_rank(row, cfg),
        ),
    )
    for row in candidates:
        if len(selected) >= target:
            break
        family = _family_id(row)
        if row["conversation_id"] in selected_ids:
            continue
        if family_counts[family] >= cfg.sample.max_family_tasks:
            continue
        duplicate_group = _duplicate_group(row)
        if duplicate_group in duplicate_groups:
            continue
        selected.append(row)
        selected_ids.add(row["conversation_id"])
        family_counts[family] += 1
        duplicate_groups.add(duplicate_group)
    return sorted(selected, key=lambda row: row["conversation_id"])


def _try_add_family_candidate(
    family: str,
    family_rows: dict[str, list[dict]],
    selected: list[dict],
    selected_ids: set[str],
    family_counts: Counter[str],
    duplicate_groups: set[object],
    cfg: EgoBenchConfig,
) -> None:
    if family_counts[family] >= cfg.sample.max_family_tasks:
        return
    for row in family_rows[family]:
        duplicate_group = _duplicate_group(row)
        if row["conversation_id"] in selected_ids or duplicate_group in duplicate_groups:
            continue
        selected.append(row)
        selected_ids.add(row["conversation_id"])
        family_counts[family] += 1
        duplicate_groups.add(duplicate_group)
        return


def _best_by_duplicate_group(rows: list[dict], cfg: EgoBenchConfig) -> list[dict]:
    groups: dict[object, list[dict]] = defaultdict(list)
    for row in rows:
        groups[_duplicate_group(row)].append(row)
    return [sorted(group, key=lambda row: _rank(row, cfg))[0] for group in groups.values()]


def _duplicate_group(row: dict) -> object:
    value = row.get("near_duplicate_group_id")
    return value if value is not None else row["conversation_id"]


def _rank(row: dict, cfg: EgoBenchConfig) -> tuple[int, int, int, int]:
    return (
        {"generalizable": 0, "narrow": 1, "one_off": 2}.get(str(row.get("specificity") or ""), 1),
        0 if row.get("difficulty") in {"medium", "hard"} else 1,
        -int(row.get("near_duplicate_group_size") or 1),
        _stable_tie(cfg.workspace.seed, row["conversation_id"]),
    )


def _stable_tie(seed: int, value: str) -> int:
    digest = hashlib.sha256(f"{seed}:{value}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


def _long_tail_target(target: int, cfg: EgoBenchConfig) -> int:
    if target <= 1 or cfg.sample.long_tail_fraction <= 0:
        return 0
    return max(1, math.floor(target * cfg.sample.long_tail_fraction))


def _duplicate_suppressed(rows: list[dict]) -> int:
    groups: Counter[object] = Counter(_duplicate_group(row) for row in rows)
    return sum(max(0, count - 1) for count in groups.values())


def _stats(rows: list[dict], selected: list[dict], duplicate_suppressed: int) -> dict[str, int]:
    return {
        "duplicate_groups_suppressed": duplicate_suppressed,
    }


def _family_id(row: dict) -> str:
    return str(row.get("task_family_id") or row.get("task_family") or "General assistance")


def _family_labels(rows: list[dict]) -> dict[str, str]:
    labels: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        labels[_family_id(row)][str(row.get("task_family") or "General assistance")] += 1
    return {
        family_id: max(counts, key=lambda label: (counts[label], label))
        for family_id, counts in labels.items()
    }
