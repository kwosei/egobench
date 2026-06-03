from __future__ import annotations

from dataclasses import dataclass

from rich.table import Table

from egobench.config import EgoBenchConfig, ModelRef
from egobench.llm.pricing import estimate_cost
from egobench.pipeline.phase4_categorize import ANNOTATION_BATCH_SIZE, CANONICAL_BATCH_SIZE
from egobench.pipeline.phase7_checklist import CHECKLIST_BATCH_SIZE


@dataclass(frozen=True)
class EstimateLine:
    phase: str
    model: ModelRef
    calls: int
    input_tokens: int
    output_tokens: int

    @property
    def cost_usd(self) -> float:
        return estimate_cost(self.model.model, self.input_tokens, self.output_tokens)


def build_estimate(
    cfg: EgoBenchConfig,
    task_count: int,
    *,
    candidate_group_sizes: list[int] | None = None,
    selected_count: int | None = None,
) -> list[EstimateLine]:
    sampled = selected_count if selected_count is not None and selected_count > 0 else min(cfg.sample.target_n, task_count)
    default = cfg.judges.default
    filter_ref = cfg.filter.model_ref
    embedding = ModelRef(provider=cfg.embeddings.provider, model=cfg.embeddings.model)
    annotation_calls = _annotation_batch_count(task_count, candidate_group_sizes)
    canonical_calls = _ceil(task_count, CANONICAL_BATCH_SIZE) if task_count > 1 else 0
    checklist_batches = _ceil(sampled, CHECKLIST_BATCH_SIZE)

    lines = [
        EstimateLine("phase2-filter", filter_ref, task_count, task_count * 60, task_count * 1),
        EstimateLine("phase3-embeddings", embedding, 1 if task_count else 0, task_count * 100, 0),
        EstimateLine(
            "phase4",
            default,
            annotation_calls + canonical_calls,
            annotation_calls * 950 + canonical_calls * 1200,
            annotation_calls * 650 + canonical_calls * 700,
        ),
    ]
    for ref in cfg.judges.checklist_panel:
        lines.append(
            EstimateLine(
                "phase7-checklist",
                ref,
                checklist_batches,
                checklist_batches * 1800,
                checklist_batches * 700,
            )
        )
    lines.append(
        EstimateLine(
            "phase7-merge",
            default,
            checklist_batches,
            checklist_batches * 2200,
            checklist_batches * 650,
        )
    )
    return lines


def eval_estimate(
    cfg: EgoBenchConfig,
    model: ModelRef,
    task_count: int,
    judge_models: list[ModelRef] | None = None,
) -> list[EstimateLine]:
    panel = judge_models or cfg.judges.eval_judges()
    lines = [EstimateLine("answer", model, task_count, task_count * 900, task_count * 700)]
    for ref in panel:
        lines.append(EstimateLine("judge", ref, task_count, task_count * 1100, task_count * 180))
    return lines


def estimate_table(lines: list[EstimateLine]) -> Table:
    table = Table(title="Estimated API cost")
    table.add_column("Phase")
    table.add_column("Model")
    table.add_column("Calls", justify="right")
    table.add_column("Input tok", justify="right")
    table.add_column("Output tok", justify="right")
    table.add_column("Cost", justify="right")
    for line in lines:
        table.add_row(
            line.phase,
            line.model.display(),
            str(line.calls),
            str(line.input_tokens),
            str(line.output_tokens),
            f"${line.cost_usd:.2f}",
        )
    table.add_section()
    table.add_row("", "", "", "", "Total", f"${sum(line.cost_usd for line in lines):.2f}")
    return table


def _annotation_batch_count(task_count: int, candidate_group_sizes: list[int] | None) -> int:
    if candidate_group_sizes:
        return sum(_ceil(size, ANNOTATION_BATCH_SIZE) for size in candidate_group_sizes)
    return _ceil(task_count, ANNOTATION_BATCH_SIZE)


def _ceil(count: int, size: int) -> int:
    if count <= 0:
        return 0
    return (count + size - 1) // size
