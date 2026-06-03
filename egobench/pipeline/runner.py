from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from rich.console import Console

from egobench.config import EgoBenchConfig
from egobench.db import DB, fetch_conversations
from egobench.llm.pricing import PricingResolver
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


PHASE3_INPUT_COLUMNS = ("conversation_id", "is_task", "first_user_text")
PHASE4_INPUT_COLUMNS = (
    "conversation_id",
    "is_task",
    "first_user_text",
    "candidate_group_id",
    "candidate_group_size",
    "near_duplicate_group_id",
    "near_duplicate_group_size",
)
PHASE5_INPUT_COLUMNS = (
    "conversation_id",
    "is_task",
    "task_family_id",
    "task_family",
    "near_duplicate_group_id",
    "difficulty",
    "specificity",
)
PHASE6_INPUT_COLUMNS = (
    "conversation_id",
    "is_task",
    "task_family_id",
    "task_family",
    "near_duplicate_group_id",
    "near_duplicate_group_size",
    "difficulty",
    "specificity",
    "family_size",
    "family_importance",
)
PHASE7_INPUT_COLUMNS = (
    "conversation_id",
    "is_task",
    "first_user_text",
    "selected",
    "task_family_id",
    "task_family",
    "domain",
    "skills_json",
    "difficulty",
    "specificity",
)
PHASE8_INPUT_COLUMNS = (
    "conversation_id",
    "is_task",
    "cluster_id",
    "cluster_size",
    "candidate_group_id",
    "candidate_group_size",
    "near_duplicate_group_id",
    "near_duplicate_group_size",
    "category_label",
    "category_description",
    "task_family_id",
    "task_family",
    "domain",
    "skills_json",
    "difficulty",
    "specificity",
    "family_size",
    "family_importance",
    "importance",
    "selected",
    "checklist_json",
)


@dataclass
class PipelineCtx:
    paths: WorkspacePaths
    db: DB
    cfg: EgoBenchConfig
    console: Console
    pricing: PricingResolver | None = None


@dataclass(frozen=True)
class PhaseStep:
    num: int
    name: str
    label: str
    input_factory: Callable[[PipelineCtx], dict]
    runner: Callable[[PipelineCtx], dict]


def run_build(ctx: PipelineCtx, *, from_phase: int | None = None) -> dict:
    if not fetch_conversations(ctx.db):
        raise RuntimeError("No conversations found. Run `egobench ingest <path>` first.")

    outputs: dict[str, dict] = {}
    for step in _phase_steps():
        outputs[step.name] = _run_cached(ctx, step, from_phase)
    return outputs


def _phase_steps() -> tuple[PhaseStep, ...]:
    return (
        PhaseStep(
            2,
            "phase2",
            "filter non-task conversations",
            lambda ctx: {"conversations": fetch_conversations(ctx.db)},
            lambda ctx: phase2_drop_nontasks.run(ctx.db, ctx.cfg, ctx.console, pricing=ctx.pricing),
        ),
        PhaseStep(
            3,
            "phase3",
            "embed and cluster task candidates",
            lambda ctx: {"tasks": state_rows(ctx.db, PHASE3_INPUT_COLUMNS)},
            lambda ctx: phase3_embed_cluster.run(ctx.db, ctx.cfg, ctx.console, pricing=ctx.pricing),
        ),
        PhaseStep(
            4,
            "phase4",
            "label task families",
            lambda ctx: {"clusters": state_rows(ctx.db, PHASE4_INPUT_COLUMNS)},
            lambda ctx: phase4_categorize.run(ctx.db, ctx.cfg, ctx.console, pricing=ctx.pricing),
        ),
        PhaseStep(
            5,
            "phase5",
            "score family importance",
            lambda ctx: {"categorized": state_rows(ctx.db, PHASE5_INPUT_COLUMNS)},
            lambda ctx: phase5_importance.run(ctx.db, ctx.console),
        ),
        PhaseStep(
            6,
            "phase6",
            "sample benchmark tasks",
            lambda ctx: {"scored": state_rows(ctx.db, PHASE6_INPUT_COLUMNS)},
            lambda ctx: phase6_sample.run(ctx.db, ctx.cfg, ctx.console),
        ),
        PhaseStep(
            7,
            "phase7",
            "draft and merge task checklists",
            lambda ctx: {"selected": state_rows(ctx.db, PHASE7_INPUT_COLUMNS)},
            lambda ctx: phase7_checklist.run(ctx.db, ctx.cfg, ctx.console, pricing=ctx.pricing),
        ),
        PhaseStep(
            8,
            "phase8",
            "lock benchmark files",
            lambda ctx: {"checked": state_rows(ctx.db, PHASE8_INPUT_COLUMNS)},
            lambda ctx: phase8_lock.run(ctx.db, ctx.cfg, ctx.paths, ctx.console),
        ),
    )


def _run_cached(
    ctx: PipelineCtx,
    step: PhaseStep,
    from_phase: int | None,
) -> dict:
    payload = step.input_factory(ctx)
    cache_key = make_cache_key(step.name, payload, ctx.cfg)
    if from_phase is not None and step.num < from_phase:
        cached = read_cache(ctx.db, step.name, cache_key)
        if cached is not None:
            ctx.console.print(f"[dim]Skipping {step.name} ({step.label}); cache key matched.[/dim]")
            return cached
        ctx.console.print(
            f"[dim]Skipping {step.name} ({step.label}); before --from {from_phase} "
            "(cache miss, using existing workspace state).[/dim]"
        )
        return {"phase": step.num, "skipped": True}

    if from_phase is None:
        cached = read_cache(ctx.db, step.name, cache_key)
        if cached is not None:
            ctx.console.print(f"[dim]Skipping {step.name} ({step.label}); cache key matched.[/dim]")
            return cached
    ctx.console.print(f"Phase {step.num}/8: {step.label} ({step.name})")
    output = step.runner(ctx)
    write_cache(ctx.db, step.name, cache_key, output)
    _write_cache_file(ctx, step.name, cache_key, output)
    ctx.console.print(f"[dim]Completed {step.name} ({step.label}): {_summary(output)}[/dim]")
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


def _summary(output: dict) -> str:
    parts = [f"{key}={value}" for key, value in output.items() if key != "phase"]
    return ", ".join(parts) if parts else "done"
