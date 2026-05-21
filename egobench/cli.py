from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Annotated

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

load_dotenv()

from egobench.config import DEFAULT_CONFIG_TEXT, ConfigError, EgoBenchConfig, ModelRef, load_config
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
def init(
    force: Annotated[bool, typer.Option("--force", help="Overwrite an existing egobench.toml with the current default.")] = False,
) -> None:
    """Create egobench-workspace and default config."""
    paths = workspace_from_cwd()
    paths.ensure()
    init_db(paths.db)
    console.print(f"Workspace: {paths.root}")

    if force or not paths.config.exists():
        paths.config.write_text(DEFAULT_CONFIG_TEXT, encoding="utf-8")
        console.print("Wrote egobench.toml" if force else "Created egobench.toml")
        return

    try:
        load_config(paths.config)
    except ConfigError as err:
        console.print("[red]egobench.toml exists but does not parse:[/red]")
        console.print(str(err), markup=False)
        console.print("Re-run with [bold]--force[/bold] to overwrite it with the current default.")
        raise typer.Exit(code=1)
    console.print("egobench.toml already exists (parsed cleanly)")


@app.command()
def ingest(
    path: Annotated[Path, typer.Argument(exists=True, readable=True)],
    adapter: Annotated[str, typer.Option("--adapter", help="auto, chatgpt, claude, or jsonl")] = "auto",
) -> None:
    """Load conversations from an export file or directory."""
    paths, _, db = _workspace()
    result = run_ingest_phase(db, path, adapter, console)
    console.print(f"Imported {result['conversations']} conversations via {result['adapter']} into {paths.db}")


@app.command()
def build(
    from_phase: Annotated[int | None, typer.Option("--from", min=2, max=8, help="Start from a specific phase.")] = None,
    estimate_only: Annotated[bool, typer.Option("--estimate-only", help="Show estimated cost and exit.")] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip cost confirmation.")] = False,
) -> None:
    """Build benchmark.json from ingested conversations."""
    paths, cfg, db = _workspace()
    console.print(_build_models_summary(cfg), markup=False)
    task_count = len(fetch_conversations(db))
    lines = build_estimate(
        cfg,
        task_count,
        candidate_group_sizes=_candidate_group_sizes(db),
        selected_count=_selected_task_count(db),
    )
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
    provider: Annotated[str, typer.Option("--provider", help="Provider of the model to benchmark (key in [providers.*]).")],
    model: Annotated[str, typer.Option("--model", help="Model id to benchmark, as the provider expects it.")],
    judge_provider: Annotated[str | None, typer.Option("--judge-provider", help="Override judge provider (requires --judge-model).")] = None,
    judge_model: Annotated[str | None, typer.Option("--judge-model", help="Override judge model id (requires --judge-provider).")] = None,
    estimate_only: Annotated[bool, typer.Option("--estimate-only")] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip cost confirmation.")] = False,
) -> None:
    """Run the benchmark against one model and score its answers."""
    paths, cfg, db = _workspace()
    benchmark = load_benchmark(paths)

    if (judge_provider is None) ^ (judge_model is None):
        raise typer.BadParameter("--judge-provider and --judge-model must be set together.")

    candidate = ModelRef(provider=provider, model=model)
    if provider not in cfg.providers:
        raise typer.BadParameter(f"Unknown provider '{provider}'. Add [providers.{provider}] to egobench.toml.")

    if judge_provider is not None:
        if judge_provider not in cfg.providers:
            raise typer.BadParameter(f"Unknown judge provider '{judge_provider}'.")
        judge_ref = ModelRef(provider=judge_provider, model=judge_model or "")
        cfg = replace(cfg, judges=replace(cfg.judges, default=judge_ref))
    else:
        judge_ref = cfg.judges.default

    lines = eval_estimate(cfg, candidate, len(benchmark.tasks))
    console.print(estimate_table(lines))
    if estimate_only:
        return
    if not yes and sum(line.cost_usd for line in lines) > 0:
        typer.confirm("Continue with eval?", abort=True)
    result = run_eval(paths, db, cfg, model=candidate, judge_model=judge_ref, console=console)
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


def _candidate_group_sizes(db: DB) -> list[int] | None:
    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT COUNT(*) AS size
            FROM task_candidates
            WHERE is_task = 1 AND candidate_group_id IS NOT NULL
            GROUP BY candidate_group_id
            """
        ).fetchall()
    sizes = [int(row["size"]) for row in rows]
    return sizes or None


def _selected_task_count(db: DB) -> int | None:
    with db.connect() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS selected
            FROM task_candidates
            WHERE is_task = 1 AND selected = 1
            """
        ).fetchone()
    selected = int(row["selected"] or 0)
    return selected or None


def _build_models_summary(cfg: EgoBenchConfig) -> str:
    embedding_ref = ModelRef(provider=cfg.embeddings.provider, model=cfg.embeddings.model)
    rows = [
        (
            "phase2 filter",
            "[filter]",
            cfg.filter.model_ref,
            _runtime_note(cfg, cfg.filter.model_ref, missing_key_fallback="recorded fallback"),
        ),
        (
            "phase3 embeddings",
            "[embeddings]",
            embedding_ref,
            _runtime_note(cfg, embedding_ref, missing_key_fallback="deterministic fallback"),
        ),
        (
            "phase4 families",
            "[judges.default]",
            cfg.judges.default,
            _runtime_note(cfg, cfg.judges.default, missing_key_fallback="recorded fallback"),
        ),
    ]
    for idx, ref in enumerate(cfg.judges.checklist_panel, start=1):
        rows.append(
            (
                f"phase7 checklist panel {idx}",
                "[[judges.checklist_panel]]",
                ref,
                _runtime_note(cfg, ref, missing_key_fallback="recorded fallback"),
            )
        )
    rows.append(
        (
            "phase7 checklist merge",
            "[judges.default]",
            cfg.judges.default,
            _runtime_note(cfg, cfg.judges.default, missing_key_fallback="recorded fallback"),
        )
    )

    lines = ["Build models:"]
    for part, config_section, ref, runtime in rows:
        lines.append(f"  {part} ({config_section}): {ref.display()} ({runtime})")
    return "\n".join(lines)


def _runtime_note(cfg: EgoBenchConfig, ref: ModelRef, *, missing_key_fallback: str) -> str:
    provider = cfg.provider(ref.provider)
    if provider.api_key_env:
        if os.environ.get(provider.api_key_env):
            return f"{provider.api_key_env} set"
        if provider.api_key_keyring:
            return f"{provider.api_key_env} unset; keyring configured"
        return f"{provider.api_key_env} unset; {missing_key_fallback}"
    if provider.api_key_keyring:
        return "keyring configured"
    return "no API key env required"


if __name__ == "__main__":
    app()
