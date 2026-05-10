from __future__ import annotations

import json

from egobench.config import EgoBenchConfig, stable_config_dict
from egobench.db import DB, fetch_conversations
from egobench.paths import WorkspacePaths
from egobench.pipeline.schema import Benchmark, BenchmarkMetadata, BenchmarkTask, TurnModel, now_iso, stable_hash


def run(db: DB, cfg: EgoBenchConfig, paths: WorkspacePaths) -> dict:
    tasks = _benchmark_tasks(db)
    config_dict = stable_config_dict(cfg)
    hash_payload = {
        "tasks": [task.model_dump(mode="json") for task in tasks],
        "config": config_dict,
        "seed": cfg.workspace.seed,
    }
    benchmark_hash = stable_hash(hash_payload)
    version = _next_version(db)
    benchmark = Benchmark(
        metadata=BenchmarkMetadata(
            version=version,
            benchmark_hash=benchmark_hash,
            task_count=len(tasks),
            generated_at=now_iso(),
            seed=cfg.workspace.seed,
            config=config_dict,
        ),
        tasks=tasks,
    )
    version_path = paths.root / f"benchmark_v{version}.json"
    text = json.dumps(benchmark.model_dump(mode="json"), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    paths.benchmark.write_text(text, encoding="utf-8")
    version_path.write_text(text, encoding="utf-8")
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO benchmark_versions(benchmark_hash, path, task_count, config_json)
            VALUES (?, ?, ?, ?)
            """,
            (benchmark_hash, str(version_path), len(tasks), json.dumps(config_dict, sort_keys=True)),
        )
    return {"phase": 8, "version": version, "benchmark_hash": benchmark_hash, "tasks": len(tasks)}


def _next_version(db: DB) -> int:
    with db.connect() as conn:
        row = conn.execute("SELECT COALESCE(MAX(version), 0) + 1 AS next_version FROM benchmark_versions").fetchone()
        return int(row["next_version"])


def _benchmark_tasks(db: DB) -> list[BenchmarkTask]:
    conversations = {conv["id"]: conv for conv in fetch_conversations(db)}
    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT conversation_id, cluster_id, cluster_size, category_label,
                   category_description, importance, checklist_json
            FROM task_candidates
            WHERE is_task = 1 AND selected = 1
            ORDER BY conversation_id
            """
        ).fetchall()
    tasks: list[BenchmarkTask] = []
    for idx, row in enumerate(rows, start=1):
        conv = conversations[row["conversation_id"]]
        turns = [TurnModel(**turn) for turn in _turns_to_last_user(conv["turns"])]
        tasks.append(
            BenchmarkTask(
                id=f"task-{idx:04d}",
                conversation_id=row["conversation_id"],
                turns=turns,
                category=row["category_label"] or "General",
                category_description=row["category_description"] or "",
                cluster_id=int(row["cluster_id"] or 0),
                cluster_size=int(row["cluster_size"] or 1),
                importance=float(row["importance"] or 0.0),
                checklist=json.loads(row["checklist_json"] or "[]"),
            )
        )
    return tasks


def _turns_to_last_user(turns: list[dict]) -> list[dict]:
    last_user = 0
    for idx, turn in enumerate(turns):
        if turn["role"] == "user":
            last_user = idx
    return turns[: last_user + 1]

