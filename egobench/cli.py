from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from egobench.config import load_config, write_default_config
from egobench.cost.estimator import build_estimate, estimate_table, eval_estimate
from egobench.db import DB, fetch_conversations, init_db
from egobench.eval.runner import load_benchmark, run_eval
from egobench.paths import workspace_from_cwd
from egobench.pipeline.phase1_ingest import run as run_ingest_phase
from egobench.pipeline.runner import PipelineCtx, run_build
from egobench.reporting.html import render_reports
from egobench.reporting.leaderboard import leaderboard_table
from egobench.review.app import run_review


app = typer.Typer(help="Build and run a local personal LLM benchmark.")
console = Console()


def _workspace() -> tuple:
    paths = workspace_from_cwd()
    paths.ensure()
    cfg = load_config(paths.config)
    db = init_db(paths.db)
    return paths, cfg, db


@app.command()
def init() -> None:
    """Create egobench-workspace and default config."""
    paths = workspace_from_cwd()
    paths.ensure()
    created = write_default_config(paths.config)
    init_db(paths.db)
    console.print(f"Workspace: {paths.root}")
    console.print("Created egobench.toml" if created else "egobench.toml already exists")


@app.command()
def ingest(
    path: Annotated[Path, typer.Argument(exists=True, readable=True)],
    adapter: Annotated[str, typer.Option("--adapter", help="auto, chatgpt, claude, or jsonl")] = "auto",
) -> None:
    """Load conversations from an export file or directory."""
    paths, _, db = _workspace()
    result = run_ingest_phase(db, path, adapter)
    console.print(f"Imported {result['conversations']} conversations via {result['adapter']} into {paths.db}")


@app.command()
def build(
    from_phase: Annotated[int | None, typer.Option("--from", min=2, max=8, help="Start from a specific phase.")] = None,
    estimate_only: Annotated[bool, typer.Option("--estimate-only", help="Show estimated cost and exit.")] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip cost confirmation.")] = False,
) -> None:
    """Build benchmark.json from ingested conversations."""
    paths, cfg, db = _workspace()
    task_count = len(fetch_conversations(db))
    lines = build_estimate(cfg, task_count)
    console.print(estimate_table(lines))
    if estimate_only:
        return
    if not yes and sum(line.cost_usd for line in lines) > 0:
        typer.confirm("Continue with paid phases?", abort=True)
    ctx = PipelineCtx(paths=paths, db=db, cfg=cfg, console=console)
    outputs = run_build(ctx, from_phase=from_phase)
    final = outputs["phase8"]
    console.print(f"Benchmark v{final['version']} written: {paths.benchmark}")
    console.print(f"Hash: {final['benchmark_hash']}")


@app.command()
def review(
    port: Annotated[int, typer.Option("--port", help="Reserved for command compatibility.")] = 8765,
) -> None:
    """Open the Textual benchmark review UI."""
    paths, cfg, db = _workspace()
    _ = port
    run_review(paths, db, cfg)


@app.command()
def eval(
    model: Annotated[str, typer.Option("--model", help="Candidate model to evaluate.")],
    judge: Annotated[str | None, typer.Option("--judge", help="Judge model override.")] = None,
    estimate_only: Annotated[bool, typer.Option("--estimate-only")] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip cost confirmation.")] = False,
) -> None:
    """Evaluate one candidate model against benchmark.json."""
    paths, cfg, db = _workspace()
    benchmark = load_benchmark(paths)
    judge_model = judge or cfg.judges.default
    if judge is not None:
        cfg = replace(cfg, judges=replace(cfg.judges, default=judge))
    lines = eval_estimate(cfg, model, len(benchmark.tasks))
    console.print(estimate_table(lines))
    if estimate_only:
        return
    if not yes and sum(line.cost_usd for line in lines) > 0:
        typer.confirm("Continue with eval?", abort=True)
    result = run_eval(paths, db, cfg, model=model, judge_model=judge_model)
    console.print(f"Run written: {result['run_dir']}")
    console.print(f"Raw EgoScore: {result['raw_egoscore']:.2f}")
    console.print(f"Freq-weighted EgoScore: {result['frequency_weighted_egoscore']:.2f}")
    console.print(f"Report: {paths.report_html}")


@app.command()
def report() -> None:
    """Regenerate report.html and report.md from local runs."""
    paths, _, _ = _workspace()
    render_reports(paths)
    console.print(f"Wrote {paths.report_html}")
    console.print(f"Wrote {paths.report_md}")


@app.command()
def leaderboard() -> None:
    """Print the local leaderboard table."""
    paths, _, _ = _workspace()
    console.print(leaderboard_table(paths))


@app.command()
def cost() -> None:
    """Summarize the cost ledger."""
    paths, _, db = _workspace()
    table = Table(title="Cost Ledger")
    table.add_column("Phase")
    table.add_column("Model")
    table.add_column("Input tok", justify="right")
    table.add_column("Output tok", justify="right")
    table.add_column("Cost", justify="right")
    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT phase, model, SUM(input_tokens) AS input_tokens,
                   SUM(output_tokens) AS output_tokens, SUM(cost_usd) AS cost
            FROM phase_cost_log
            GROUP BY phase, model
            ORDER BY phase, model
            """
        ).fetchall()
    for row in rows:
        table.add_row(
            row["phase"],
            row["model"],
            str(row["input_tokens"]),
            str(row["output_tokens"]),
            f"${float(row['cost'] or 0):.4f}",
        )
    console.print(table)
    console.print(f"Workspace: {paths.root}")


@app.command()
def refresh(
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip cost confirmation.")] = False,
) -> None:
    """Rebuild the benchmark from currently ingested conversations."""
    build(from_phase=2, estimate_only=False, yes=yes)


if __name__ == "__main__":
    app()
