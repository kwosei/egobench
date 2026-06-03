from __future__ import annotations

from dataclasses import dataclass

from rich.table import Table

from egobench.config import EgoBenchConfig, ModelRef, ProviderCfg
from egobench.llm.pricing import PriceQuote, PricingResolver, quote_for_model
from egobench.pipeline.phase4_categorize import (
    ANNOTATION_BATCH_SIZE,
    CANONICAL_BATCH_SIZE,
)
from egobench.pipeline.phase7_checklist import CHECKLIST_BATCH_SIZE


@dataclass(frozen=True)
class EstimateLine:
    phase: str
    model: ModelRef
    calls: int
    input_tokens: int
    output_tokens: int
    quote: PriceQuote

    @property
    def cost_usd(self) -> float:
        return self.quote.cost_usd(self.input_tokens, self.output_tokens)

    @property
    def price_known(self) -> bool:
        return self.quote.known

    @property
    def price_approximate(self) -> bool:
        return self.quote.approximate

    @property
    def price_label(self) -> str:
        return self.quote.price_label

    @property
    def cost_label(self) -> str:
        return self.quote.cost_label(self.input_tokens, self.output_tokens)


def build_estimate(
    cfg: EgoBenchConfig,
    task_count: int,
    *,
    candidate_group_sizes: list[int] | None = None,
    selected_count: int | None = None,
    pricing: PricingResolver | None = None,
) -> list[EstimateLine]:
    sampled = (
        selected_count
        if selected_count is not None and selected_count > 0
        else min(cfg.sample.target_n, task_count)
    )
    default = cfg.judges.default
    filter_ref = cfg.filter.model_ref
    embedding = ModelRef(provider=cfg.embeddings.provider, model=cfg.embeddings.model)
    annotation_calls = _annotation_batch_count(task_count, candidate_group_sizes)
    canonical_calls = _ceil(task_count, CANONICAL_BATCH_SIZE) if task_count > 1 else 0
    checklist_batches = _ceil(sampled, CHECKLIST_BATCH_SIZE)

    lines = [
        _estimate_line(
            cfg,
            "phase2-filter",
            filter_ref,
            task_count,
            task_count * 60,
            task_count * 1,
            pricing,
        ),
        _estimate_line(
            cfg,
            "phase3-embeddings",
            embedding,
            1 if task_count else 0,
            task_count * 100,
            0,
            pricing,
        ),
        _estimate_line(
            cfg,
            "phase4",
            default,
            annotation_calls + canonical_calls,
            annotation_calls * 950 + canonical_calls * 1200,
            annotation_calls * 650 + canonical_calls * 700,
            pricing,
        ),
    ]
    for ref in cfg.judges.checklist_panel:
        lines.append(
            _estimate_line(
                cfg,
                "phase7-checklist",
                ref,
                checklist_batches,
                checklist_batches * 1800,
                checklist_batches * 700,
                pricing,
            )
        )
    lines.append(
        _estimate_line(
            cfg,
            "phase7-merge",
            default,
            checklist_batches,
            checklist_batches * 2200,
            checklist_batches * 650,
            pricing,
        )
    )
    return lines


def eval_estimate(
    cfg: EgoBenchConfig,
    model: ModelRef,
    task_count: int,
    judge_models: list[ModelRef] | None = None,
    pricing: PricingResolver | None = None,
) -> list[EstimateLine]:
    panel = judge_models or cfg.judges.eval_judges()
    lines = [
        _estimate_line(
            cfg,
            "answer",
            model,
            task_count,
            task_count * 900,
            task_count * 700,
            pricing,
        )
    ]
    for ref in panel:
        lines.append(
            _estimate_line(
                cfg,
                "judge",
                ref,
                task_count,
                task_count * 1100,
                task_count * 180,
                pricing,
            )
        )
    return lines


def estimate_table(lines: list[EstimateLine]) -> Table:
    table = Table(title="Estimated API cost")
    table.add_column("Phase")
    table.add_column("Model")
    table.add_column("Calls", justify="right")
    table.add_column("Input tok", justify="right")
    table.add_column("Output tok", justify="right")
    table.add_column("Pricing source")
    table.add_column("Cost", justify="right")
    for line in lines:
        table.add_row(
            line.phase,
            line.model.display(),
            str(line.calls),
            str(line.input_tokens),
            str(line.output_tokens),
            line.price_label,
            line.cost_label,
        )
    table.add_section()
    prefix = "≈" if has_approximate_prices(lines) else ""
    total = f"{prefix}${sum(line.cost_usd for line in lines):.2f}"
    if has_unknown_prices(lines):
        total = f"{total} + unknown"
    table.add_row("", "", "", "", "", "Total", total)
    return table


def known_cost_total(lines: list[EstimateLine]) -> float:
    return sum(line.cost_usd for line in lines)


def has_unknown_prices(lines: list[EstimateLine]) -> bool:
    return any(not line.price_known for line in lines)


def has_approximate_prices(lines: list[EstimateLine]) -> bool:
    return any(line.price_approximate for line in lines)


def _estimate_line(
    cfg: EgoBenchConfig,
    phase: str,
    model: ModelRef,
    calls: int,
    input_tokens: int,
    output_tokens: int,
    pricing: PricingResolver | None,
) -> EstimateLine:
    provider = cfg.providers.get(model.provider)
    return EstimateLine(
        phase,
        model,
        calls,
        input_tokens,
        output_tokens,
        quote=_quote_for_ref(provider, model, pricing),
    )


def _quote_for_ref(
    provider: ProviderCfg | None, ref: ModelRef, pricing: PricingResolver | None
) -> PriceQuote:
    if (
        provider is not None
        and provider.api_key_env is None
        and provider.api_key_keyring is None
    ):
        return quote_for_model(
            ref.model, provider=ref.provider, resolver=pricing, local=True
        )
    return quote_for_model(ref.model, provider=ref.provider, resolver=pricing)


def _annotation_batch_count(
    task_count: int, candidate_group_sizes: list[int] | None
) -> int:
    if candidate_group_sizes:
        return sum(_ceil(size, ANNOTATION_BATCH_SIZE) for size in candidate_group_sizes)
    return _ceil(task_count, ANNOTATION_BATCH_SIZE)


def _ceil(count: int, size: int) -> int:
    if count <= 0:
        return 0
    return (count + size - 1) // size
