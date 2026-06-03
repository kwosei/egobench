from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import replace
from enum import Enum
from pathlib import Path
from typing import Annotated, Iterable

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from egobench.config import DEFAULT_CONFIG_TEXT, ConfigError, EgoBenchConfig, ModelRef, ProviderCfg, load_config
from egobench.cost.estimator import (
    build_estimate,
    EstimateLine,
    estimate_table,
    eval_estimate,
    has_approximate_prices,
    has_unknown_prices,
    known_cost_total,
)
from egobench.db import DB, fetch_conversations, init_db
from egobench.eval.runner import load_benchmark, run_eval
from egobench.llm.pricing import PricingResolver
from egobench.paths import WorkspacePaths, workspace_from_cwd
from egobench.pipeline.phase1_ingest import run as run_ingest_phase
from egobench.pipeline.runner import PipelineCtx, run_build
from egobench.reporting.html import render_reports
from egobench.reporting.leaderboard import leaderboard_table, load_run_summaries
from egobench.review.app import run_review


app = typer.Typer(help="Build and run a local personal LLM benchmark.")
console = Console()


class AdapterName(str, Enum):
    auto = "auto"
    chatgpt = "chatgpt"
    claude = "claude"
    jsonl = "jsonl"


def _workspace() -> tuple[WorkspacePaths, EgoBenchConfig, DB, Path | None]:
    paths = workspace_from_cwd()
    paths.ensure()
    env_path = _load_env_file()
    try:
        cfg = load_config(paths.config)
    except ConfigError as err:
        console.print("[red]egobench.toml does not parse:[/red]")
        console.print(str(err), markup=False)
        console.print("Next: fix egobench-workspace/egobench.toml, then run [bold]egobench status[/bold].")
        raise typer.Exit(code=1) from err
    db = init_db(paths.db)
    return paths, cfg, db, env_path


@app.command()
def init(
    force: Annotated[bool, typer.Option("--force", help="Overwrite an existing egobench.toml with the current default.")] = False,
) -> None:
    """Create egobench-workspace and default config."""
    paths = workspace_from_cwd()
    env_path = _load_env_file()
    paths.ensure()
    init_db(paths.db)
    console.print(f"Workspace: {paths.root}")
    console.print(_env_source_text(env_path), markup=False)

    if force or not paths.config.exists():
        paths.config.write_text(DEFAULT_CONFIG_TEXT, encoding="utf-8")
        console.print("Wrote egobench.toml" if force else "Created egobench.toml")
        console.print("Next: add API keys to .env if needed, then run `egobench ingest <export-path> --adapter auto`.")
        return

    try:
        load_config(paths.config)
    except ConfigError as err:
        console.print("[red]egobench.toml exists but does not parse:[/red]")
        console.print(str(err), markup=False)
        console.print("Re-run with [bold]--force[/bold] to overwrite it with the current default.")
        raise typer.Exit(code=1)
    console.print("egobench.toml already exists (parsed cleanly)")
    console.print("Next: run `egobench status` to see what is ready.")


@app.command("doctor")
@app.command("status")
def status() -> None:
    """Show workspace readiness and the next recommended command."""
    paths = workspace_from_cwd()
    env_path = _load_env_file()

    cfg: EgoBenchConfig | None = None
    config_state = "missing"
    config_details = f"{paths.config} does not exist"
    if paths.config.exists():
        try:
            cfg = load_config(paths.config)
            config_state = "valid"
            config_details = str(paths.config)
        except ConfigError as err:
            config_state = "invalid"
            config_details = str(err)

    conversations = _count_rows(paths.db, "conversations")
    benchmark_tasks = _benchmark_task_count(paths.benchmark)
    runs = len(load_run_summaries(paths))

    table = Table(title="EgoBench Status")
    table.add_column("Area")
    table.add_column("State")
    table.add_column("Details")
    table.add_row(
        "Workspace",
        "ready" if paths.root.exists() else "missing",
        str(paths.root),
    )
    table.add_row("Environment", "loaded" if env_path else "shell only", _env_source_text(env_path))
    table.add_row("Config", config_state, config_details)
    table.add_row(
        "Conversations",
        str(conversations) if conversations is not None else "none",
        str(paths.db) if paths.db.exists() else "database not created yet",
    )
    table.add_row(
        "Benchmark",
        f"{benchmark_tasks} tasks" if benchmark_tasks is not None else "missing",
        str(paths.benchmark),
    )
    table.add_row("Runs", str(runs), str(paths.runs_dir))
    if cfg is not None:
        table.add_row("Providers", str(len(cfg.providers)), "\n".join(_provider_status_lines(cfg)))

    console.print(table)
    console.print(f"Next: {_next_step(paths, config_state, conversations, benchmark_tasks, runs)}", markup=False)


@app.command()
def ingest(
    path: Annotated[Path, typer.Argument(exists=True, readable=True)],
    adapter: Annotated[AdapterName, typer.Option("--adapter", help="Input format. Use auto to detect it.")] = AdapterName.auto,
) -> None:
    """Load conversations from an export file or directory."""
    paths, _, db, _ = _workspace()
    try:
        result = run_ingest_phase(db, path, adapter.value, console)
    except ValueError as err:
        console.print("[red]Could not ingest export:[/red]")
        console.print(str(err), markup=False)
        console.print("Next: retry with `--adapter chatgpt`, `--adapter claude`, or `--adapter jsonl`.")
        raise typer.Exit(code=1) from err
    console.print(f"Imported {result['conversations']} conversations via {result['adapter']} into {paths.db}")
    console.print("Next: run `egobench build --dry-run` to preview model calls and cost.")


@app.command()
def build(
    from_phase: Annotated[int | None, typer.Option("--from", min=2, max=8, help="Start from a specific phase.")] = None,
    estimate_only: Annotated[
        bool,
        typer.Option("--estimate-only", "--dry-run", help="Show estimated cost and exit without calling APIs."),
    ] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip cost confirmation.")] = False,
) -> None:
    """Build benchmark.json from ingested conversations."""
    paths, cfg, db, env_path = _workspace()
    task_count = len(fetch_conversations(db))
    if task_count == 0:
        console.print("[red]No conversations found.[/red]")
        console.print("Next: run `egobench ingest <export-path> --adapter auto`.")
        raise typer.Exit(code=1)

    console.print(_env_source_text(env_path), markup=False)
    console.print(_build_models_summary(cfg), markup=False)
    pricing = _pricing_resolver(paths, cfg)
    lines = build_estimate(
        cfg,
        task_count,
        candidate_group_sizes=_candidate_group_sizes(db),
        selected_count=_selected_task_count(db),
        pricing=pricing,
    )
    console.print(estimate_table(lines))
    _print_price_note(lines)
    estimated_cost = known_cost_total(lines)
    has_unknown_cost = has_unknown_prices(lines)
    has_approximate_cost = has_approximate_prices(lines)
    if estimate_only:
        console.print("Dry run only; no APIs were called.")
        console.print("Next: run `egobench build` when the estimate and routing look right.")
        return
    if not yes and (estimated_cost > 0 or has_unknown_cost):
        console.print(
            _api_confirmation_panel(
                title="Before Build",
                estimated_cost=estimated_cost,
                has_unknown_cost=has_unknown_cost,
                has_approximate_cost=has_approximate_cost,
                env_path=env_path,
                cfg=cfg,
                refs=_build_model_refs(cfg),
                deterministic_fallback_refs={(cfg.embeddings.provider, cfg.embeddings.model)},
                data_notice=(
                    "Build may send conversation prompts and derived task metadata to the configured "
                    "filter, embedding, and judge providers. Artifacts are written under egobench-workspace/."
                ),
            )
        )
        typer.confirm("Continue with build?", abort=True)
    elif yes and (estimated_cost > 0 or has_unknown_cost):
        console.print(
            _api_confirmation_panel(
                title="Build Routing",
                estimated_cost=estimated_cost,
                has_unknown_cost=has_unknown_cost,
                has_approximate_cost=has_approximate_cost,
                env_path=env_path,
                cfg=cfg,
                refs=_build_model_refs(cfg),
                deterministic_fallback_refs={(cfg.embeddings.provider, cfg.embeddings.model)},
                data_notice=(
                    "Build may send conversation prompts and derived task metadata to the configured "
                    "filter, embedding, and judge providers. Artifacts are written under egobench-workspace/."
                ),
            )
        )
    ctx = PipelineCtx(paths=paths, db=db, cfg=cfg, console=console, pricing=pricing)
    try:
        outputs = run_build(ctx, from_phase=from_phase)
    except RuntimeError as err:
        _exit_for_runtime_error(err)
    final = outputs["phase8"]
    console.print(f"Benchmark v{final['version']} written: {paths.benchmark}")
    console.print(f"Hash: {final['benchmark_hash']}")
    console.print("Next: run `egobench review` to inspect tasks, or `egobench eval --model <provider/model-id> --dry-run`.")


@app.command()
def review(
    port: Annotated[int, typer.Option("--port", hidden=True)] = 8765,
) -> None:
    """Open the Textual benchmark review UI."""
    paths, cfg, db, _ = _workspace()
    _ = port
    if not paths.benchmark.exists():
        console.print("[red]No benchmark.json found.[/red]")
        console.print("Next: run `egobench build` first.")
        raise typer.Exit(code=1)
    try:
        run_review(paths, db, cfg)
    except RuntimeError as err:
        _exit_for_runtime_error(err)


@app.command()
def eval(
    model: Annotated[
        str,
        typer.Option(
            "--model",
            help="Model to benchmark as provider/model-id (e.g. openai/gpt-5 or openrouter/anthropic/claude-sonnet-4).",
        ),
    ],
    judge: Annotated[
        list[str] | None,
        typer.Option(
            "--judge",
            help="Judge as provider/model-id (e.g. openai/gpt-5). Repeat for a panel; overrides the configured scoring panel.",
        ),
    ] = None,
    estimate_only: Annotated[
        bool,
        typer.Option("--estimate-only", "--dry-run", help="Show estimated cost and exit without calling APIs."),
    ] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip cost confirmation.")] = False,
) -> None:
    """Run the benchmark against one model and score its answers."""
    paths, cfg, db, env_path = _workspace()
    candidate = _parse_cli_model_ref(model, cfg.providers, "--model")
    if not paths.benchmark.exists():
        console.print("[red]No benchmark.json found.[/red]")
        console.print("Next: run `egobench build` first.")
        raise typer.Exit(code=1)
    try:
        benchmark = load_benchmark(paths)
    except RuntimeError as err:
        _exit_for_runtime_error(err)

    judge_panel = _resolve_judge_panel(cfg, candidate, judge)

    console.print(_env_source_text(env_path), markup=False)
    pricing = _pricing_resolver(paths, cfg)
    lines = eval_estimate(cfg, candidate, len(benchmark.tasks), judge_panel, pricing=pricing)
    console.print(estimate_table(lines))
    _print_price_note(lines)
    estimated_cost = known_cost_total(lines)
    has_unknown_cost = has_unknown_prices(lines)
    has_approximate_cost = has_approximate_prices(lines)
    if estimate_only:
        console.print("Dry run only; no APIs were called.")
        console.print("Next: run the same `egobench eval` command without `--dry-run` when ready.")
        return
    if not yes and (estimated_cost > 0 or has_unknown_cost):
        console.print(
            _api_confirmation_panel(
                title="Before Eval",
                estimated_cost=estimated_cost,
                has_unknown_cost=has_unknown_cost,
                has_approximate_cost=has_approximate_cost,
                env_path=env_path,
                cfg=cfg,
                refs=[candidate, *judge_panel],
                data_notice=(
                    "Eval sends benchmark tasks to the candidate provider and sends candidate responses, "
                    "task prompts, and checklists to the judge provider."
                ),
            )
        )
        typer.confirm("Continue with eval?", abort=True)
    elif yes and (estimated_cost > 0 or has_unknown_cost):
        console.print(
            _api_confirmation_panel(
                title="Eval Routing",
                estimated_cost=estimated_cost,
                has_unknown_cost=has_unknown_cost,
                has_approximate_cost=has_approximate_cost,
                env_path=env_path,
                cfg=cfg,
                refs=[candidate, *judge_panel],
                data_notice=(
                    "Eval sends benchmark tasks to the candidate provider and sends candidate responses, "
                    "task prompts, and checklists to the judge provider."
                ),
            )
        )
    try:
        result = run_eval(
            paths,
            db,
            cfg,
            model=candidate,
            judge_models=judge_panel,
            console=console,
            pricing=pricing,
        )
    except RuntimeError as err:
        _exit_for_runtime_error(err)
    console.print(f"Run written: {result['run_dir']}")
    console.print(f"Raw EgoScore: {result['raw_egoscore']:.2f}")
    console.print(f"Freq-weighted EgoScore: {result['frequency_weighted_egoscore']:.2f}")
    console.print(f"Report: {paths.report_html}")
    console.print("Next: run `egobench leaderboard` or open egobench-workspace/report.html.")


@app.command()
def report() -> None:
    """Regenerate report.html and report.md from local runs."""
    paths, _, _, _ = _workspace()
    render_reports(paths)
    console.print(f"Wrote {paths.report_html}")
    console.print(f"Wrote {paths.report_md}")
    console.print("Next: open egobench-workspace/report.html in your browser.")


@app.command()
def leaderboard() -> None:
    """Print the local leaderboard table."""
    paths, _, _, _ = _workspace()
    console.print(leaderboard_table(paths))
    if not load_run_summaries(paths):
        console.print("No eval runs found yet. Next: run `egobench eval --model <provider/model-id> --dry-run`.")


@app.command()
def cost() -> None:
    """Summarize the cost ledger."""
    paths, _, db, _ = _workspace()
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
    if not rows:
        console.print("No cost records found yet.")
    console.print(f"Workspace: {paths.root}")


@app.command()
def refresh(
    estimate_only: Annotated[
        bool,
        typer.Option("--estimate-only", "--dry-run", help="Show estimated cost and exit without calling APIs."),
    ] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip cost confirmation.")] = False,
) -> None:
    """Rebuild the benchmark from currently ingested conversations."""
    build(from_phase=2, estimate_only=estimate_only, yes=yes)


def _load_env_file() -> Path | None:
    env_path = Path.cwd() / ".env"
    if env_path.exists():
        load_dotenv(env_path)
        return env_path
    return None


def _env_source_text(env_path: Path | None) -> str:
    if env_path is not None:
        return f"Environment: loaded {env_path}"
    return f"Environment: shell only; no .env at {Path.cwd() / '.env'}"


def _count_rows(db_path: Path, table: str) -> int | None:
    if not db_path.exists():
        return None
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
            row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
    except sqlite3.Error:
        return None
    return int(row[0]) if row else 0


def _benchmark_task_count(path: Path) -> int | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    metadata_count = payload.get("metadata", {}).get("task_count")
    if metadata_count is not None:
        return int(metadata_count)
    return len(payload.get("tasks", []))


def _next_step(
    paths: WorkspacePaths,
    config_state: str,
    conversations: int | None,
    benchmark_tasks: int | None,
    runs: int,
) -> str:
    if not paths.root.exists() or config_state == "missing":
        return "`egobench init`"
    if config_state == "invalid":
        return "fix egobench-workspace/egobench.toml, then run `egobench status`"
    if conversations is None or conversations == 0:
        return "`egobench ingest <export-path> --adapter auto`"
    if benchmark_tasks is None:
        return "`egobench build --dry-run`, then `egobench build`"
    if runs == 0:
        return "`egobench eval --model <provider/model-id> --dry-run`"
    return "`egobench leaderboard` or open egobench-workspace/report.html"


def _provider_status_lines(cfg: EgoBenchConfig) -> list[str]:
    lines: list[str] = []
    for name in sorted(cfg.providers):
        provider = cfg.providers[name]
        if provider.api_key_env is None:
            lines.append(f"{name}: no API key env required")
        elif os.environ.get(provider.api_key_env):
            lines.append(f"{name}: {provider.api_key_env} set; live API calls enabled")
        elif provider.api_key_keyring:
            lines.append(f"{name}: {provider.api_key_env} unset; keyring configured")
        else:
            lines.append(f"{name}: {provider.api_key_env} unset; fallback client will be used")
    return lines


def _api_confirmation_panel(
    *,
    title: str,
    estimated_cost: float,
    env_path: Path | None,
    cfg: EgoBenchConfig,
    refs: Iterable[ModelRef],
    data_notice: str,
    has_unknown_cost: bool = False,
    has_approximate_cost: bool = False,
    deterministic_fallback_refs: set[tuple[str, str]] | None = None,
) -> Panel:
    deterministic_fallback_refs = deterministic_fallback_refs or set()
    prefix = "≈" if has_approximate_cost else ""
    estimate_label = f"{prefix}${estimated_cost:.2f}"
    if has_unknown_cost:
        estimate_label = f"{estimate_label} + unknown model pricing"
    lines = [
        f"Estimated cost: {estimate_label}",
        _env_source_text(env_path),
        "",
        "Runtime routing:",
        *[
            f"  {ref.display()}: {_runtime_mode(cfg, ref, deterministic_fallback_refs)}"
            for ref in _unique_model_refs(refs)
        ],
        "",
        f"Data notice: {data_notice}",
    ]
    return Panel("\n".join(lines), title=title, expand=False)


def _print_price_note(lines: list[EstimateLine]) -> None:
    if has_approximate_prices(lines) or has_unknown_prices(lines):
        console.print(
            "[yellow]Some model prices are approximate or unknown; live provider charges may differ.[/yellow]"
        )


def _pricing_resolver(paths: WorkspacePaths, cfg: EgoBenchConfig) -> PricingResolver:
    return PricingResolver.from_config(cfg, cache_dir=paths.cache_dir / "pricing", fetch_external=True)


def _runtime_mode(cfg: EgoBenchConfig, ref: ModelRef, deterministic_fallback_refs: set[tuple[str, str]]) -> str:
    provider = cfg.provider(ref.provider)
    if provider.api_key_env is None:
        return "local or unauthenticated OpenAI-compatible endpoint"
    if os.environ.get(provider.api_key_env):
        return f"live provider via {provider.api_key_env}"
    if provider.api_key_keyring:
        return "keyring configured"
    if (ref.provider, ref.model) in deterministic_fallback_refs:
        return "deterministic fallback because API key is unset"
    return "recorded fallback because API key is unset"


def _build_model_refs(cfg: EgoBenchConfig) -> list[ModelRef]:
    return [
        cfg.filter.model_ref,
        ModelRef(provider=cfg.embeddings.provider, model=cfg.embeddings.model),
        cfg.judges.default,
        *cfg.judges.checklist_panel,
        cfg.judges.default,
    ]


def _unique_model_refs(refs: Iterable[ModelRef]) -> list[ModelRef]:
    unique: list[ModelRef] = []
    seen: set[tuple[str, str]] = set()
    for ref in refs:
        key = (ref.provider, ref.model)
        if key in seen:
            continue
        seen.add(key)
        unique.append(ref)
    return unique


def _resolve_judge_panel(
    cfg: EgoBenchConfig, candidate: ModelRef, judge_specs: list[str] | None
) -> list[ModelRef]:
    """Resolve the scoring panel: explicit --judge wins, else config.

    Explicit --judge judges are used verbatim. Otherwise we take the configured
    scoring panel (or [judges.default]) and, when exclude_candidate_provider is
    set, drop any judge sharing the candidate's provider so a model never grades
    itself — erroring if that would leave no judges.
    """
    if judge_specs:
        return _unique_model_refs(_parse_cli_model_ref(spec, cfg.providers, "--judge") for spec in judge_specs)
    panel = cfg.judges.eval_judges()
    if cfg.judges.exclude_candidate_provider:
        filtered = [ref for ref in panel if ref.provider != candidate.provider]
        if not filtered:
            raise typer.BadParameter(
                f"Scoring panel is empty after excluding the candidate's provider "
                f"'{candidate.provider}' (exclude_candidate_provider = true). Add a "
                f"[[judges.scoring_panel]] judge from another provider, or pass --judge."
            )
        panel = filtered
    return _unique_model_refs(panel)


def _parse_cli_model_ref(spec: str, providers: dict[str, ProviderCfg], option_name: str) -> ModelRef:
    provider, sep, model_id = spec.partition("/")
    provider, model_id = provider.strip(), model_id.strip()
    if not sep or not provider or not model_id:
        raise typer.BadParameter(f"{option_name} '{spec}' must be provider/model-id, e.g. openai/gpt-5.")
    if provider not in providers:
        raise typer.BadParameter(
            f"Unknown provider '{provider}' in {option_name} '{spec}'. Add [providers.{provider}] to egobench.toml."
        )
    return ModelRef(provider=provider, model=model_id)


def _exit_for_runtime_error(err: RuntimeError) -> None:
    message = str(err)
    console.print("[red]Command cannot continue:[/red]")
    console.print(message, markup=False)
    if "No conversations found" in message:
        console.print("Next: run `egobench ingest <export-path> --adapter auto`.")
    elif "No benchmark.json found" in message:
        console.print("Next: run `egobench build` first.")
    elif "does not match the latest SQLite benchmark snapshot" in message:
        console.print("Next: run `egobench build` to relock the benchmark, then retry.")
    else:
        console.print("Next: run `egobench status` to inspect workspace readiness.")
    raise typer.Exit(code=1) from err


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
