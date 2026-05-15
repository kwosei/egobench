from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from rich.console import Console

from egobench.config import EgoBenchConfig
from egobench.db import DB, fetch_conversations
from egobench.paths import WorkspacePaths
from egobench.pipeline import (
    phase2_drop_nontasks,
    phase3_embed_cluster,
    phase4_categorize,
    phase5_importance,
    phase6_sample,
    phase7_checklist,
    phase8_lock,
)
from egobench.pipeline.cache import make_cache_key, read_cache, state_rows, write_cache


@dataclass
class PipelineCtx:
    paths: WorkspacePaths
    db: DB
    cfg: EgoBenchConfig
    console: Console


def run_build(ctx: PipelineCtx, *, from_phase: int | None = None) -> dict:
    if not fetch_conversations(ctx.db):
        raise RuntimeError("No conversations found. Run `egobench ingest <path>` first.")

    outputs: dict[str, dict] = {}
    outputs["phase2"] = _run_cached(
        ctx,
        2,
        "phase2",
        lambda: {"conversations": fetch_conversations(ctx.db)},
        lambda: phase2_drop_nontasks.run(ctx.db),
        from_phase,
    )
    outputs["phase3"] = _run_cached(
        ctx,
        3,
        "phase3",
        lambda: {"tasks": state_rows(ctx.db)},
        lambda: phase3_embed_cluster.run(ctx.db, ctx.cfg),
        from_phase,
    )
    outputs["phase4"] = _run_cached(
        ctx,
        4,
        "phase4",
        lambda: {"clusters": state_rows(ctx.db)},
        lambda: phase4_categorize.run(ctx.db, ctx.cfg, ctx.console),
        from_phase,
    )
    outputs["phase5"] = _run_cached(
        ctx,
        5,
        "phase5",
        lambda: {"categorized": state_rows(ctx.db)},
        lambda: phase5_importance.run(ctx.db),
        from_phase,
    )
    outputs["phase6"] = _run_cached(
        ctx,
        6,
        "phase6",
        lambda: {"scored": state_rows(ctx.db)},
        lambda: phase6_sample.run(ctx.db, ctx.cfg),
        from_phase,
    )
    outputs["phase7"] = _run_cached(
        ctx,
        7,
        "phase7",
        lambda: {"selected": state_rows(ctx.db)},
        lambda: phase7_checklist.run(ctx.db, ctx.cfg, ctx.console),
        from_phase,
    )
    outputs["phase8"] = _run_cached(
        ctx,
        8,
        "phase8",
        lambda: {"checked": state_rows(ctx.db)},
        lambda: phase8_lock.run(ctx.db, ctx.cfg, ctx.paths),
        from_phase,
    )
    return outputs


def _run_cached(
    ctx: PipelineCtx,
    phase_num: int,
    phase_name: str,
    input_factory: Callable[[], dict],
    runner: Callable[[], dict],
    from_phase: int | None,
) -> dict:
    payload = input_factory()
    cache_key = make_cache_key(phase_name, payload, ctx.cfg)
    if from_phase is None or phase_num < from_phase:
        cached = read_cache(ctx.db, phase_name, cache_key)
        if cached is not None:
            ctx.console.print(f"[dim]Skipping {phase_name}; cache key matched.[/dim]")
            return cached
    ctx.console.print(f"Running {phase_name}...")
    output = runner()
    write_cache(ctx.db, phase_name, cache_key, output)
    _write_cache_file(ctx, phase_name, cache_key, output)
    return output


def _write_cache_file(ctx: PipelineCtx, phase_name: str, cache_key: str, output: dict) -> None:
    ctx.paths.cache_dir.mkdir(parents=True, exist_ok=True)
    path = ctx.paths.cache_dir / f"{phase_name}.json"
    path.write_text(
        stable_cache_text({"phase": phase_name, "cache_key": cache_key, "output": output}),
        encoding="utf-8",
    )


def stable_cache_text(payload: dict) -> str:
    import json

    return json.dumps(payload, indent=2, sort_keys=True) + "\n"
