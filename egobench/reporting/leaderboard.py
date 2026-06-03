from __future__ import annotations

import json
from pathlib import Path

from rich.table import Table

from egobench.paths import WorkspacePaths


def format_judges(row: dict) -> str:
    """Human-readable label for the judges that scored a run.

    Single judge: just its ``provider:model``. A panel: the judges joined,
    suffixed with the aggregation method (``· mean``/``· median``) since the
    consensus score depends on it. Legacy runs without a ``judges`` field
    return ``—`` rather than implying a judge that was never recorded.
    """
    judges = [str(judge) for judge in row.get("judges", []) if judge]
    if not judges:
        return "—"
    names = ", ".join(judges)
    if len(judges) == 1:
        return names
    aggregate = str(row.get("scoring_aggregate", "mean"))
    return f"{names} · {aggregate}"


def load_run_summaries(paths: WorkspacePaths) -> list[dict]:
    summaries: list[dict] = []
    if not paths.runs_dir.exists():
        return summaries
    for summary_path in sorted(paths.runs_dir.glob("*/*/summary.json")):
        payload = json.loads(summary_path.read_text(encoding="utf-8"))
        payload["_path"] = str(summary_path.parent)
        summaries.append(payload)
    return sorted(summaries, key=lambda row: row.get("frequency_weighted_egoscore", 0), reverse=True)


def leaderboard_table(paths: WorkspacePaths) -> Table:
    table = Table(title="EgoBench Leaderboard")
    table.add_column("Model")
    table.add_column("Raw", justify="right")
    table.add_column("Freq-weighted", justify="right")
    table.add_column("Cost", justify="right")
    table.add_column("Judges")
    table.add_column("Run")
    for row in load_run_summaries(paths):
        table.add_row(
            row["model"],
            f"{row['raw_egoscore']:.2f}",
            f"{row['frequency_weighted_egoscore']:.2f}",
            f"${row.get('run_cost_usd', 0):.4f}",
            format_judges(row),
            Path(row["_path"]).name,
        )
    return table

