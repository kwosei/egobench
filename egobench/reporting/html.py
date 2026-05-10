from __future__ import annotations

import json
from pathlib import Path

from jinja2 import Template

from egobench.paths import WorkspacePaths
from egobench.reporting.leaderboard import load_run_summaries
from egobench.reporting.markdown import render_markdown
from egobench.reporting.radar import bar_svg, radar_svg


HTML_TEMPLATE = Template(
    """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>EgoBench Report</title>
<style>
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 0; color: #111827; background: #f8fafc; }
header, main { max-width: 1100px; margin: 0 auto; padding: 24px; }
header { padding-top: 32px; }
h1 { margin: 0 0 8px; font-size: 32px; }
h2 { margin-top: 32px; }
table { width: 100%; border-collapse: collapse; background: white; border: 1px solid #e5e7eb; }
th, td { padding: 10px 12px; border-bottom: 1px solid #e5e7eb; text-align: left; }
th:nth-child(n+2), td:nth-child(n+2) { text-align: right; }
.panel { background: white; border: 1px solid #e5e7eb; padding: 16px; margin: 16px 0; }
.charts { display: grid; grid-template-columns: minmax(260px, 320px) 1fr; gap: 18px; align-items: start; }
details { background: white; border: 1px solid #e5e7eb; margin: 8px 0; padding: 10px 12px; }
summary { cursor: pointer; font-weight: 600; }
input { width: min(420px, 100%); padding: 10px 12px; border: 1px solid #cbd5e1; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; background: #f1f5f9; padding: 10px; }
.muted { color: #6b7280; }
@media (max-width: 760px) { .charts { grid-template-columns: 1fr; } header, main { padding: 16px; } }
</style>
</head>
<body>
<header>
<h1>EgoBench Report</h1>
<div class="muted">Benchmark {{ benchmark_hash }} · {{ task_count }} tasks</div>
</header>
<main>
<h2>Leaderboard</h2>
<table>
<thead><tr><th>Model</th><th>Raw EgoScore</th><th>Freq-weighted</th><th>Run cost</th><th>Wall time</th></tr></thead>
<tbody>
{% for row in runs %}
<tr><td>{{ row.model }}</td><td>{{ "%.2f"|format(row.raw_egoscore) }}</td><td>{{ "%.2f"|format(row.frequency_weighted_egoscore) }}</td><td>${{ "%.4f"|format(row.run_cost_usd) }}</td><td>{{ "%.1f"|format(row.wall_time_seconds) }}s</td></tr>
{% endfor %}
</tbody>
</table>
<h2>Breakdown</h2>
<div class="charts panel">
<div>{{ radar|safe }}</div>
<div>{{ bars|safe }}</div>
</div>
<h2>Drill-down</h2>
<input id="search" placeholder="Search tasks, responses, rationales">
<div id="tasks">
{% for item in details %}
<details data-search="{{ item.search }}">
<summary>{{ item.model }} · {{ item.task_id }} · score {{ item.score }}</summary>
<p><strong>Prompt</strong></p><pre>{{ item.prompt }}</pre>
<p><strong>Response</strong></p><pre>{{ item.response }}</pre>
<p><strong>Checklist</strong></p><ul>{% for check in item.checklist %}<li>{{ check }}</li>{% endfor %}</ul>
<p><strong>Rationale</strong></p><pre>{{ item.rationale }}</pre>
</details>
{% endfor %}
</div>
<p class="muted">Evaluation replays prior turns as stored in the original export and asks the candidate to answer at the final user turn.</p>
</main>
<script>
const search = document.getElementById('search');
search.addEventListener('input', () => {
  const q = search.value.toLowerCase();
  document.querySelectorAll('#tasks details').forEach((el) => {
    el.style.display = el.dataset.search.includes(q) ? '' : 'none';
  });
});
</script>
</body>
</html>
"""
)


def render_reports(paths: WorkspacePaths) -> None:
    paths.root.mkdir(parents=True, exist_ok=True)
    runs = load_run_summaries(paths)
    details = _load_details(paths)
    benchmark_hash, task_count = _benchmark_meta(paths)
    categories = sorted({category for row in runs for category in row.get("per_category", {})})
    first = runs[0].get("per_category", {}) if runs else {}
    html = HTML_TEMPLATE.render(
        runs=runs,
        details=details,
        benchmark_hash=benchmark_hash[:12] if benchmark_hash else "none",
        task_count=task_count,
        radar=radar_svg(runs, categories),
        bars=bar_svg(first),
    )
    paths.report_html.write_text(html, encoding="utf-8")
    paths.report_md.write_text(render_markdown(paths), encoding="utf-8")


def _benchmark_meta(paths: WorkspacePaths) -> tuple[str, int]:
    if not paths.benchmark.exists():
        return "", 0
    payload = json.loads(paths.benchmark.read_text(encoding="utf-8"))
    meta = payload.get("metadata", {})
    return str(meta.get("benchmark_hash", "")), int(meta.get("task_count", 0))


def _load_details(paths: WorkspacePaths) -> list[dict]:
    items: list[dict] = []
    for run_dir in sorted(paths.runs_dir.glob("*/*")):
        tasks = _jsonl_by_id(run_dir / "tasks.jsonl")
        responses = _jsonl_by_id(run_dir / "responses.jsonl")
        scores = _jsonl_by_id(run_dir / "scores.jsonl")
        rationales = _jsonl_by_id(run_dir / "rationales.jsonl")
        summary_path = run_dir / "summary.json"
        if not summary_path.exists():
            continue
        model = json.loads(summary_path.read_text(encoding="utf-8")).get("model", run_dir.parent.name)
        for task_id, task in tasks.items():
            response = responses.get(task_id, {}).get("response", "")
            score = scores.get(task_id, {}).get("score", "")
            rationale = rationales.get(task_id, {}).get("rationale", "")
            search = " ".join([model, task_id, task.get("prompt", ""), response, rationale]).lower()
            items.append(
                {
                    "model": model,
                    "task_id": task_id,
                    "prompt": task.get("prompt", ""),
                    "checklist": task.get("checklist", []),
                    "response": response,
                    "score": score,
                    "rationale": rationale,
                    "search": search,
                }
            )
    return items


def _jsonl_by_id(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    rows = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        rows[payload["task_id"]] = payload
    return rows

