from __future__ import annotations

from egobench.paths import WorkspacePaths
from egobench.reporting.leaderboard import format_judges, load_run_summaries


def render_markdown(paths: WorkspacePaths) -> str:
    rows = load_run_summaries(paths)
    lines = [
        "# EgoBench Report",
        "",
        "| Model | Raw EgoScore | Freq-weighted EgoScore | Cost | Judges |",
        "|---|---:|---:|---:|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row['model']} | {row['raw_egoscore']:.2f} | "
            f"{row['frequency_weighted_egoscore']:.2f} | ${row.get('run_cost_usd', 0):.4f} | "
            f"{format_judges(row)} |"
        )
    lines.append("")
    lines.append("## Per-category Means")
    for row in rows:
        lines.append("")
        lines.append(f"### {row['model']}")
        for category, value in sorted(row.get("per_category", {}).items()):
            lines.append(f"- {category}: {value:.2f}")
    return "\n".join(lines) + "\n"

