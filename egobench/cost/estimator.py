from __future__ import annotations

from dataclasses import dataclass

from rich.table import Table

from egobench.config import EgoBenchConfig
from egobench.llm.pricing import estimate_cost


@dataclass(frozen=True)
class EstimateLine:
    phase: str
    model: str
    calls: int
    input_tokens: int
    output_tokens: int

    @property
    def cost_usd(self) -> float:
        return estimate_cost(self.model, self.input_tokens, self.output_tokens)


def build_estimate(cfg: EgoBenchConfig, task_count: int) -> list[EstimateLine]:
    edge_cases = max(0, int(task_count * 0.1))
    sampled = min(cfg.sample.target_n, task_count)
    lines = [
        EstimateLine("phase2", cfg.judges.default, edge_cases, edge_cases * 250, edge_cases * 20),
        EstimateLine("phase4", cfg.judges.default, max(1, task_count // 8), max(1, task_count // 8) * 700, max(1, task_count // 8) * 80),
    ]
    for model in cfg.judges.checklist_panel:
        lines.append(EstimateLine("phase7-checklist", model, sampled, sampled * 600, sampled * 220))
    lines.append(EstimateLine("phase7-merge", cfg.judges.default, sampled, sampled * 500, sampled * 180))
    return lines


def eval_estimate(cfg: EgoBenchConfig, model: str, task_count: int) -> list[EstimateLine]:
    return [
        EstimateLine("candidate", model, task_count, task_count * 900, task_count * 700),
        EstimateLine("judge", cfg.judges.default, task_count, task_count * 1100, task_count * 180),
    ]


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
            line.model,
            str(line.calls),
            str(line.input_tokens),
            str(line.output_tokens),
            f"${line.cost_usd:.2f}",
        )
    table.add_section()
    table.add_row("", "", "", "", "Total", f"${sum(line.cost_usd for line in lines):.2f}")
    return table
