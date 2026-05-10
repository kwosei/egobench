from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from egobench.config import EgoBenchConfig
from egobench.db import DB, latest_benchmark_hash
from egobench.eval.judge import judge_response
from egobench.eval.score import compute_scores
from egobench.llm.factory import make_client
from egobench.paths import WorkspacePaths
from egobench.pipeline.schema import Benchmark, BenchmarkTask
from egobench.reporting.html import render_reports


def run_eval(paths: WorkspacePaths, db: DB, cfg: EgoBenchConfig, *, model: str, judge_model: str) -> dict:
    benchmark = load_benchmark(paths)
    expected_hash = latest_benchmark_hash(db)
    if expected_hash and expected_hash != benchmark.metadata.benchmark_hash:
        raise RuntimeError("benchmark.json does not match the latest SQLite benchmark snapshot.")

    started = time.monotonic()
    before_cost_id = _max_cost_id(db)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = paths.runs_dir / _safe_model_name(model) / timestamp
    run_dir.mkdir(parents=True, exist_ok=True)

    score_rows: list[dict] = []
    with (
        (run_dir / "tasks.jsonl").open("a", encoding="utf-8") as tasks_file,
        (run_dir / "responses.jsonl").open("a", encoding="utf-8") as responses_file,
        (run_dir / "scores.jsonl").open("a", encoding="utf-8") as scores_file,
        (run_dir / "rationales.jsonl").open("a", encoding="utf-8") as rationales_file,
    ):
        for task in benchmark.tasks:
            prompt = task_prompt(task)
            response = call_candidate(db, cfg, model, task)
            judged = judge_response(
                db=db,
                cfg=cfg,
                judge_model=judge_model,
                task_prompt=prompt,
                checklist=task.checklist,
                response=response,
            )
            score_row = {
                "task_id": task.id,
                "conversation_id": task.conversation_id,
                "category": task.category,
                "cluster_size": task.cluster_size,
                "score": judged["score"],
            }
            rationale_row = {
                "task_id": task.id,
                "strengths": judged["strengths"],
                "weaknesses": judged["weaknesses"],
                "rationale": judged["rationale"],
            }
            tasks_file.write(json.dumps({"task_id": task.id, "prompt": prompt, "checklist": task.checklist}) + "\n")
            responses_file.write(json.dumps({"task_id": task.id, "response": response}, ensure_ascii=False) + "\n")
            scores_file.write(json.dumps(score_row, sort_keys=True) + "\n")
            rationales_file.write(json.dumps(rationale_row, ensure_ascii=False, sort_keys=True) + "\n")
            tasks_file.flush()
            responses_file.flush()
            scores_file.flush()
            rationales_file.flush()
            score_rows.append(score_row)

    summary = compute_scores(score_rows)
    cost_usd = _cost_since(db, before_cost_id)
    payload = {
        "model": model,
        "judge": judge_model,
        "benchmark_hash": benchmark.metadata.benchmark_hash,
        "task_count": len(score_rows),
        "raw_egoscore": summary.raw,
        "frequency_weighted_egoscore": summary.frequency_weighted,
        "per_category": summary.per_category,
        "run_cost_usd": round(cost_usd, 6),
        "wall_time_seconds": round(time.monotonic() - started, 3),
        "created_at": timestamp,
    }
    (run_dir / "summary.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    render_reports(paths)
    return {"run_dir": str(run_dir), **payload}


def load_benchmark(paths: WorkspacePaths) -> Benchmark:
    if not paths.benchmark.exists():
        raise RuntimeError("No benchmark.json found. Run `egobench build` first.")
    return Benchmark.model_validate_json(paths.benchmark.read_text(encoding="utf-8"))


def task_prompt(task: BenchmarkTask) -> str:
    return "\n".join(f"{turn.role.upper()}: {turn.text}" for turn in task.turns)


def call_candidate(db: DB, cfg: EgoBenchConfig, model: str, task: BenchmarkTask) -> str:
    client = make_client(model, cfg, db, "eval-candidate")
    prompt = task_prompt(task)
    if cfg.api_key_for_model(model):
        completion = client.complete(prompt)
        return completion.text.strip()
    completion = client.complete(f"Return CANDIDATE_RESPONSE_JSON with key response.\n<TASK>\n{prompt}\n</TASK>")
    try:
        return json.loads(completion.text).get("response", completion.text).strip()
    except Exception:
        return completion.text.strip()


def _safe_model_name(model: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "_", model).strip("_") or "model"


def _max_cost_id(db: DB) -> int:
    with db.connect() as conn:
        row = conn.execute("SELECT COALESCE(MAX(id), 0) AS max_id FROM phase_cost_log").fetchone()
        return int(row["max_id"])


def _cost_since(db: DB, cost_id: int) -> float:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) AS cost FROM phase_cost_log WHERE id > ?",
            (cost_id,),
        ).fetchone()
        return float(row["cost"] or 0.0)

