from __future__ import annotations

import json
from pathlib import Path

from jinja2 import Template

from egobench.paths import WorkspacePaths
from egobench.reporting.leaderboard import format_judges, load_run_summaries
from egobench.reporting.markdown import render_markdown
from egobench.reporting.radar import bar_svg


HTML_TEMPLATE = Template(
    autoescape=True,
    source="""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>EgoBench Report</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
<style>
:root {
  --color-cloud-white: #ffffff;
  --color-canvas-fog: #fafaf9;
  --color-slate-text: #0c0a09;
  --color-ash-gray: #78716c;
  --color-stone-border: #e5e7eb;
  --color-platinum-outline: #d6d3d1;
  --color-steel-gray: #a8a29e;
  --color-hover-stone: #c9c5c2;
  --color-chartwell-blue: #3ba6f1;
  --color-sky-tint: #c1e1f7;
  --color-mint: #ecfdf5;
  --color-mint-text: #047857;
  --color-amber: #fffbeb;
  --color-amber-text: #b45309;
  --color-rose: #fef2f2;
  --color-rose-text: #b91c1c;
  --shadow-md: rgba(0, 0, 0, 0.05) 0px 4px 16px 0px;
  --shadow-subtle: rgba(0, 0, 0, 0.05) 0px 1px 2px 0px;
  --radius-md: 4px;
  --radius-lg: 8px;
  --radius-full: 9999px;
  --font-sans: 'Inter', ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  --font-mono: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
}

* { box-sizing: border-box; }

html, body {
  margin: 0;
  padding: 0;
  background: var(--color-canvas-fog);
  color: var(--color-slate-text);
  font-family: var(--font-sans);
  font-size: 14px;
  line-height: 1.5;
  -webkit-font-smoothing: antialiased;
  text-rendering: optimizeLegibility;
}

a { color: var(--color-chartwell-blue); text-decoration: none; }
a:hover { text-decoration: underline; }

.shell {
  width: 100%;
  max-width: 1200px;
  margin: 0 auto;
  padding: 32px 24px 96px;
}

.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 16px;
  margin-bottom: 32px;
}

.brand {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}
.brand-mark {
  width: 28px;
  height: 28px;
  border-radius: 8px;
  background: var(--color-slate-text);
  display: grid;
  place-items: center;
  color: var(--color-cloud-white);
  font-weight: 600;
  font-size: 13px;
  letter-spacing: 0;
}
.brand-text {
  font-size: 15px;
  font-weight: 600;
  letter-spacing: 0;
}

.pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 12px;
  background: var(--color-cloud-white);
  border: 1px solid var(--color-stone-border);
  border-radius: var(--radius-full);
  font-size: 12px;
  color: var(--color-ash-gray);
  box-shadow: var(--shadow-subtle);
  max-width: 100%;
  overflow-wrap: anywhere;
}
.pill .dot {
  width: 6px;
  height: 6px;
  border-radius: 9999px;
  background: var(--color-chartwell-blue);
}

.hero {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-bottom: 32px;
  max-width: 680px;
}
.hero h1 {
  margin: 0;
  font-size: 32px;
  line-height: 1.12;
  letter-spacing: 0;
  font-weight: 500;
}
.hero .sub {
  color: var(--color-ash-gray);
  font-size: 15px;
  max-width: 560px;
}

.section { margin-top: 40px; min-width: 0; }
.section-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  flex-wrap: wrap;
  margin-bottom: 16px;
  gap: 12px;
}
.section-head h2 {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
  letter-spacing: 0;
}
.section-head .hint {
  color: var(--color-ash-gray);
  font-size: 13px;
  max-width: 100%;
}

.kpi-grid {
  display: grid;
  grid-template-columns: minmax(0, 2fr) repeat(3, minmax(0, 1fr));
  gap: 16px;
}
@media (max-width: 880px) { .kpi-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 560px) { .kpi-grid { grid-template-columns: 1fr; } }

.card {
  background: var(--color-cloud-white);
  border: 1px solid var(--color-stone-border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-md);
  min-width: 0;
}
.card-pad { padding: 20px 24px; }
.card-pad-sm { padding: 16px 20px; }

.kpi { display: flex; flex-direction: column; gap: 6px; }
.kpi-label {
  font-size: 12px;
  letter-spacing: 0;
  color: var(--color-ash-gray);
  text-transform: uppercase;
}
.kpi-value {
  font-size: 24px;
  font-weight: 600;
  letter-spacing: 0;
  color: var(--color-slate-text);
  overflow-wrap: anywhere;
}
.kpi-sub {
  font-size: 12px;
  color: var(--color-steel-gray);
}

.table-wrap {
  overflow-x: auto;
  max-width: 100%;
}
table.leaderboard {
  width: 100%;
  min-width: 1120px;
  border-collapse: separate;
  border-spacing: 0;
  font-size: 14px;
  table-layout: fixed;
}
table.leaderboard th:nth-child(1), table.leaderboard td:nth-child(1) { width: 56px; }
table.leaderboard th:nth-child(2), table.leaderboard td:nth-child(2) { width: 280px; }
table.leaderboard th:nth-child(3), table.leaderboard td:nth-child(3) { width: 200px; }
table.leaderboard th:nth-child(4), table.leaderboard td:nth-child(4) { width: 80px; }
table.leaderboard th:nth-child(5), table.leaderboard td:nth-child(5) { width: 100px; }
table.leaderboard th:nth-child(6), table.leaderboard td:nth-child(6) { width: 110px; }
table.leaderboard th:nth-child(7), table.leaderboard td:nth-child(7) { width: 240px; }
table.leaderboard td.judges {
  color: var(--color-ash-gray);
  font-size: 12.5px;
  overflow-wrap: anywhere;
}
table.leaderboard thead th {
  text-align: left;
  font-size: 12px;
  font-weight: 500;
  color: var(--color-ash-gray);
  text-transform: uppercase;
  letter-spacing: 0;
  padding: 14px 16px;
  background: var(--color-canvas-fog);
  border-bottom: 1px solid var(--color-stone-border);
  position: sticky;
  top: 0;
}
table.leaderboard th.num, table.leaderboard td.num {
  text-align: right;
  font-variant-numeric: tabular-nums;
}
table.leaderboard tbody td {
  padding: 14px 16px;
  border-bottom: 1px solid var(--color-stone-border);
  color: var(--color-slate-text);
}
table.leaderboard tbody tr:last-child td { border-bottom: 0; }
table.leaderboard tbody tr:hover td { background: var(--color-canvas-fog); }
.rank {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 24px;
  height: 24px;
  padding: 0 7px;
  border-radius: var(--radius-full);
  background: var(--color-canvas-fog);
  border: 1px solid var(--color-stone-border);
  color: var(--color-ash-gray);
  font-size: 12px;
  font-weight: 500;
  font-variant-numeric: tabular-nums;
}
.rank.lead {
  background: var(--color-sky-tint);
  border-color: transparent;
  color: var(--color-slate-text);
}
.model-cell {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
  width: 100%;
}
.model-name {
  font-weight: 500;
  letter-spacing: 0;
  color: var(--color-slate-text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  min-width: 0;
  max-width: 100%;
}
.score-bar {
  position: relative;
  width: 120px;
  height: 6px;
  background: var(--color-canvas-fog);
  border-radius: var(--radius-full);
  overflow: hidden;
  display: inline-block;
  margin-right: 8px;
  vertical-align: middle;
}
.score-bar > span {
  display: block;
  height: 100%;
  background: var(--color-chartwell-blue);
  border-radius: var(--radius-full);
}
.score-cell { display: flex; align-items: center; justify-content: flex-end; gap: 12px; }
.score-val { font-variant-numeric: tabular-nums; font-weight: 500; }

.charts-card { padding: 24px; }
.charts-tabs {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-bottom: 16px;
}
.tab {
  appearance: none;
  border: 0;
  background: transparent;
  padding: 6px 12px;
  font-family: inherit;
  font-size: 13px;
  font-weight: 500;
  color: var(--color-ash-gray);
  cursor: pointer;
  border-radius: var(--radius-md);
}
.tab:hover { color: var(--color-slate-text); background: var(--color-canvas-fog); }
.tab[aria-selected="true"] {
  color: var(--color-slate-text);
  background: rgba(120,114,109,0.10);
}
.charts-body { min-width: 0; }
#chart-bars svg {
  max-width: 100%;
  height: auto;
}

.legend {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 14px;
  margin-top: 12px;
}
.legend-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--color-ash-gray);
}
.legend-swatch {
  width: 10px;
  height: 10px;
  border-radius: 3px;
  flex-shrink: 0;
}

.filters {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  margin-bottom: 16px;
}
.input-wrap {
  position: relative;
  flex: 1 1 320px;
  min-width: 220px;
}
.input-wrap svg {
  position: absolute;
  left: 12px;
  top: 50%;
  transform: translateY(-50%);
  color: var(--color-ash-gray);
}
.input {
  width: 100%;
  height: 36px;
  padding: 0 12px 0 36px;
  font-family: inherit;
  font-size: 14px;
  color: var(--color-slate-text);
  background: var(--color-cloud-white);
  border: 1px solid var(--color-platinum-outline);
  border-radius: 6px;
  outline: none;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}
.input:focus {
  border-color: var(--color-chartwell-blue);
  box-shadow: 0 0 0 3px rgba(59,166,241,0.18);
}
.input::placeholder { color: var(--color-ash-gray); }
.select {
  height: 36px;
  padding: 0 32px 0 12px;
  font-family: inherit;
  font-size: 14px;
  color: var(--color-slate-text);
  background: var(--color-cloud-white)
    url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%2378716c' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><polyline points='6 9 12 15 18 9'/></svg>")
    no-repeat right 10px center;
  border: 1px solid var(--color-platinum-outline);
  border-radius: 6px;
  outline: none;
  appearance: none;
  cursor: pointer;
  max-width: 100%;
}
.select:focus {
  border-color: var(--color-chartwell-blue);
  box-shadow: 0 0 0 3px rgba(59,166,241,0.18);
}
.count-tag {
  font-size: 12px;
  color: var(--color-ash-gray);
  padding: 6px 10px;
}

.task-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.task {
  background: var(--color-cloud-white);
  border: 1px solid var(--color-stone-border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-subtle);
  overflow: hidden;
}
.task summary {
  list-style: none;
  cursor: pointer;
  padding: 14px 20px;
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: center;
  gap: 16px;
}
.task summary::-webkit-details-marker { display: none; }
.task summary:hover { background: var(--color-canvas-fog); }
.task .summary-id {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--color-ash-gray);
  background: var(--color-canvas-fog);
  border: 1px solid var(--color-stone-border);
  padding: 3px 8px;
  border-radius: var(--radius-md);
}
.task .summary-main {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.task .summary-prompt {
  font-size: 14px;
  color: var(--color-slate-text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.task .summary-meta {
  font-size: 12px;
  color: var(--color-ash-gray);
}
.task .summary-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  justify-content: flex-end;
}
.chevron {
  color: var(--color-ash-gray);
  transition: transform 0.2s ease;
}
.task[open] .chevron { transform: rotate(180deg); }

.badge {
  display: inline-flex;
  align-items: center;
  height: 22px;
  padding: 0 10px;
  border-radius: var(--radius-full);
  font-size: 12px;
  font-weight: 500;
  font-variant-numeric: tabular-nums;
  border: 1px solid transparent;
}
.badge-score-high { background: var(--color-mint); color: var(--color-mint-text); }
.badge-score-mid  { background: var(--color-amber); color: var(--color-amber-text); }
.badge-score-low  { background: var(--color-rose); color: var(--color-rose-text); }
.badge-model {
  background: var(--color-cloud-white);
  border-color: var(--color-stone-border);
  color: var(--color-slate-text);
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
@media (max-width: 640px) {
  .task summary {
    grid-template-columns: auto minmax(0, 1fr);
    align-items: start;
  }
  .task .summary-actions {
    grid-column: 2;
    justify-content: flex-start;
    flex-wrap: wrap;
  }
  .badge-model { max-width: 100%; }
}

.task-body {
  padding: 8px 20px 20px;
  border-top: 1px solid var(--color-stone-border);
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 20px;
}
@media (max-width: 880px) { .task-body { grid-template-columns: 1fr; } }
.task-body .full { grid-column: 1 / -1; }
.task-section h4 {
  margin: 16px 0 8px;
  font-size: 11px;
  font-weight: 600;
  color: var(--color-ash-gray);
  text-transform: uppercase;
  letter-spacing: 0;
}
.task-section pre {
  margin: 0;
  padding: 12px 14px;
  background: var(--color-canvas-fog);
  border: 1px solid var(--color-stone-border);
  border-radius: var(--radius-md);
  font-family: var(--font-mono);
  font-size: 12.5px;
  line-height: 1.55;
  color: var(--color-slate-text);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  max-height: 360px;
  overflow: auto;
}
.checklist {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.checklist li {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  font-size: 13.5px;
  color: var(--color-slate-text);
}
.checklist li::before {
  content: "";
  display: inline-block;
  width: 14px;
  height: 14px;
  margin-top: 3px;
  flex-shrink: 0;
  background: var(--color-sky-tint);
  border-radius: 4px;
}

.judges { display: flex; flex-direction: column; gap: 12px; }
.judge {
  border: 1px solid var(--color-stone-border);
  border-radius: var(--radius-md);
  padding: 10px 12px;
  background: var(--color-cloud-white);
}
.judge-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 6px;
}
.judge-name {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--color-ash-gray);
  overflow-wrap: anywhere;
}
.spread-tag {
  font-size: 11px;
  font-weight: 500;
  color: var(--color-ash-gray);
  text-transform: none;
}

.empty {
  background: var(--color-cloud-white);
  border: 1px dashed var(--color-stone-border);
  border-radius: var(--radius-lg);
  padding: 32px;
  text-align: center;
  color: var(--color-ash-gray);
}

.footer-note {
  margin-top: 48px;
  color: var(--color-ash-gray);
  font-size: 12px;
  letter-spacing: 0;
  text-align: center;
}

.hidden { display: none !important; }
</style>
</head>
<body>
<div class="shell">

<div class="topbar">
  <div class="brand">
    <div class="brand-mark">E</div>
    <div class="brand-text">EgoBench</div>
  </div>
  <span class="pill"><span class="dot"></span>Benchmark {{ benchmark_hash_short or "none" }}</span>
</div>

<div class="hero">
  <h1>Evaluation report</h1>
  <div class="sub">{{ run_count }} run{{ "" if run_count == 1 else "s" }} across {{ task_count }} task{{ "" if task_count == 1 else "s" }}. Ranked by frequency-weighted EgoScore.</div>
</div>

<section class="section">
  <div class="kpi-grid">
    <div class="card card-pad kpi">
      <div class="kpi-label">Top model</div>
      <div class="kpi-value">{{ top_model_name or "—" }}</div>
      <div class="kpi-sub">{{ "%.2f"|format(top_score) if top_score is not none else "no runs scored" }} freq-weighted</div>
    </div>
    <div class="card card-pad kpi">
      <div class="kpi-label">Tasks</div>
      <div class="kpi-value">{{ task_count }}</div>
      <div class="kpi-sub">Locked benchmark</div>
    </div>
    <div class="card card-pad kpi">
      <div class="kpi-label">Models evaluated</div>
      <div class="kpi-value">{{ run_count }}</div>
      <div class="kpi-sub">Sorted by score</div>
    </div>
    <div class="card card-pad kpi">
      <div class="kpi-label">Total run cost</div>
      <div class="kpi-value">${{ "%.4f"|format(total_cost) }}</div>
      <div class="kpi-sub">{{ "%.1f"|format(total_wall) }}s wall time</div>
    </div>
  </div>
</section>

<section class="section">
  <div class="section-head">
    <h2>Leaderboard</h2>
    <span class="hint">Frequency-weighted ranking{% if shared_judges_label %} · judged by {{ shared_judges_label }}{% endif %}</span>
  </div>
  <div class="card table-wrap">
    {% if runs %}
    <table class="leaderboard">
      <thead>
        <tr>
          <th style="width: 56px;">#</th>
          <th>Model</th>
          <th class="num" style="width: 220px;">Freq-weighted</th>
          <th class="num">Raw</th>
          <th class="num">Cost</th>
          <th class="num">Wall time</th>
          <th>Judged by</th>
        </tr>
      </thead>
      <tbody>
      {% for row in runs %}
        <tr>
          <td><span class="rank {% if loop.index == 1 %}lead{% endif %}">{{ loop.index }}</span></td>
          <td>
            <div class="model-cell">
              <span class="legend-swatch" style="background: {{ row._color }}"></span>
              <span class="model-name">{{ row.model }}</span>
            </div>
          </td>
          <td class="num">
            <div class="score-cell">
              <span class="score-bar"><span style="width: {{ row._fw_pct }}%"></span></span>
              <span class="score-val">{{ "%.2f"|format(row.frequency_weighted_egoscore) }}</span>
            </div>
          </td>
          <td class="num">{{ "%.2f"|format(row.raw_egoscore) }}</td>
          <td class="num">${{ "%.4f"|format(row.run_cost_usd or 0) }}</td>
          <td class="num">{{ "%.1f"|format(row.wall_time_seconds or 0) }}s</td>
          <td class="judges">{{ row._judges_label }}</td>
        </tr>
      {% endfor %}
      </tbody>
    </table>
    {% else %}
    <div class="empty">No runs scored yet. Run <code>egobench eval</code> to populate this view.</div>
    {% endif %}
  </div>
</section>

{% if runs and categories %}
<section class="section">
  <div class="section-head">
    <h2>Per-category breakdown</h2>
    <span class="hint">0 – 10 score per category</span>
  </div>
  <div class="card charts-card">
    <div class="charts-tabs" role="tablist" id="chart-tabs">
      <button type="button" class="tab" role="tab" data-model="__all__" aria-selected="true">All models</button>
      {% for row in runs %}
      <button type="button" class="tab" role="tab" data-model="{{ row._chart_key }}" aria-selected="false">{{ row._display_label }}</button>
      {% endfor %}
    </div>
    <div class="charts-body">
      <div id="chart-bars">{{ bars_all|safe }}</div>
    </div>
    <div class="legend">
      {% for row in runs %}
      <span class="legend-item"><span class="legend-swatch" style="background: {{ row._color }}"></span>{{ row._display_label }}</span>
      {% endfor %}
    </div>
  </div>
</section>
{% endif %}

<section class="section">
  <div class="section-head">
    <h2>Drill-down</h2>
    <span class="hint">{{ details|length }} task response{{ "" if details|length == 1 else "s" }}</span>
  </div>
  <div class="filters">
    <div class="input-wrap">
      <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></svg>
      <input class="input" id="search" type="search" placeholder="Search tasks, prompts, responses, rationales…" autocomplete="off">
    </div>
    <select class="select" id="model-filter" aria-label="Filter by model">
      <option value="">All models</option>
      {% for model in model_options %}
      <option value="{{ model }}">{{ model }}</option>
      {% endfor %}
    </select>
    <select class="select" id="score-filter" aria-label="Filter by score">
      <option value="">All scores</option>
      <option value="high">High (≥ 8)</option>
      <option value="mid">Mid (4–7.99)</option>
      <option value="low">Low (&lt; 4)</option>
    </select>
    <select class="select" id="sort-by" aria-label="Sort order">
      <option value="score-desc">Score: high → low</option>
      <option value="score-asc">Score: low → high</option>
      <option value="task-asc">Task ID</option>
      <option value="model-asc">Model</option>
    </select>
    <span class="count-tag"><span id="visible-count">{{ details|length }}</span> shown</span>
  </div>

  <div class="task-list" id="tasks">
    {% if details %}
    {% for item in details %}
    <details class="task"
             data-search="{{ item.search }}"
             data-model="{{ item.model }}"
             data-score="{{ item.score_num }}"
             data-task="{{ item.task_id }}">
      <summary>
        <span class="summary-id">{{ item.task_id }}</span>
        <span class="summary-main">
          <span class="summary-prompt">{{ item.prompt_preview }}</span>
          <span class="summary-meta">{{ item.model }}{% if item.checklist %} · {{ item.checklist|length }} checks{% endif %}</span>
        </span>
        <span class="summary-actions">
          <span class="badge badge-model">{{ item.model }}</span>
          <span class="badge {{ item.score_tier_class }}">{{ item.score_display }}</span>
          <svg class="chevron" xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>
        </span>
      </summary>
      <div class="task-body">
        <div class="task-section full">
          <h4>Prompt</h4>
          <pre>{{ item.prompt }}</pre>
        </div>
        <div class="task-section">
          <h4>Response</h4>
          <pre>{{ item.response }}</pre>
        </div>
        <div class="task-section">
          <h4>{% if item.judges %}Judges{% else %}Rationale{% endif %}{% if item.judges|length > 1 and item.judge_spread is not none %} <span class="spread-tag">· spread {{ item.judge_spread }}</span>{% endif %}</h4>
          {% if item.judges %}
          <div class="judges">
            {% for j in item.judges %}
            <div class="judge">
              <div class="judge-head">
                <span class="judge-name">{{ j.judge }}</span>
                <span class="badge {{ j.score_tier_class }}">{{ j.score_display }}</span>
              </div>
              <pre>{{ j.rationale }}</pre>
            </div>
            {% endfor %}
          </div>
          {% else %}
          <pre>{{ item.rationale }}</pre>
          {% endif %}
        </div>
        {% if item.checklist %}
        <div class="task-section full">
          <h4>Checklist</h4>
          <ul class="checklist">
            {% for check in item.checklist %}<li>{{ check }}</li>{% endfor %}
          </ul>
        </div>
        {% endif %}
      </div>
    </details>
    {% endfor %}
    {% else %}
    <div class="empty">No task-level detail available. Evaluate a model to populate per-task responses.</div>
    {% endif %}
  </div>

  <div id="empty-state" class="empty hidden">No tasks match these filters.</div>
</section>

<p class="footer-note">Evaluation replays prior turns from the original export and asks the candidate to answer at the final user turn.</p>

</div>

<script>
(function () {
  const search = document.getElementById('search');
  const modelFilter = document.getElementById('model-filter');
  const scoreFilter = document.getElementById('score-filter');
  const sortBy = document.getElementById('sort-by');
  const list = document.getElementById('tasks');
  const empty = document.getElementById('empty-state');
  const counter = document.getElementById('visible-count');
  if (!list) return;
  const tasks = Array.from(list.querySelectorAll('.task'));

  function scoreNum(el) {
    const v = parseFloat(el.dataset.score);
    return Number.isFinite(v) ? v : -1;
  }
  function tier(v) {
    if (v >= 8) return 'high';
    if (v >= 4) return 'mid';
    if (v >= 0) return 'low';
    return '';
  }
  function apply() {
    const q = (search.value || '').toLowerCase().trim();
    const m = modelFilter.value;
    const s = scoreFilter.value;
    let visible = 0;
    tasks.forEach((el) => {
      const matchesSearch = !q || el.dataset.search.includes(q);
      const matchesModel = !m || el.dataset.model === m;
      const matchesScore = !s || tier(scoreNum(el)) === s;
      const show = matchesSearch && matchesModel && matchesScore;
      el.style.display = show ? '' : 'none';
      if (show) visible += 1;
    });
    counter.textContent = visible;
    empty.classList.toggle('hidden', visible !== 0 || tasks.length === 0);
  }
  function sortList() {
    const mode = sortBy.value;
    const sorted = tasks.slice().sort((a, b) => {
      if (mode === 'score-desc') return scoreNum(b) - scoreNum(a);
      if (mode === 'score-asc') return scoreNum(a) - scoreNum(b);
      if (mode === 'task-asc') return a.dataset.task.localeCompare(b.dataset.task);
      if (mode === 'model-asc') {
        const cmp = a.dataset.model.localeCompare(b.dataset.model);
        return cmp !== 0 ? cmp : scoreNum(b) - scoreNum(a);
      }
      return 0;
    });
    sorted.forEach((el) => list.appendChild(el));
  }
  search.addEventListener('input', apply);
  modelFilter.addEventListener('change', apply);
  scoreFilter.addEventListener('change', apply);
  sortBy.addEventListener('change', () => { sortList(); apply(); });
  sortList();
  apply();

  const tabs = document.getElementById('chart-tabs');
  const barsHost = document.getElementById('chart-bars');
  if (tabs && barsHost) {
    const charts = {{ charts_json|safe }};
    tabs.addEventListener('click', (event) => {
      const btn = event.target.closest('.tab');
      if (!btn) return;
      const key = btn.dataset.model;
      tabs.querySelectorAll('.tab').forEach((t) => t.setAttribute('aria-selected', t === btn ? 'true' : 'false'));
      const entry = charts[key] || charts.__all__;
      if (entry) { barsHost.innerHTML = entry.bars; }
    });
  }
})();
</script>
</body>
</html>
"""
)


# Same palette as radar/bar so legend swatches line up.
_SERIES_COLORS = ["#3ba6f1", "#0c0a09", "#7c3aed", "#059669", "#ea580c", "#0891b2"]


def render_reports(paths: WorkspacePaths) -> None:
    paths.root.mkdir(parents=True, exist_ok=True)
    runs = load_run_summaries(paths)
    run_counts = _run_counts_by_model(runs)
    for idx, row in enumerate(runs):
        row["_color"] = _SERIES_COLORS[idx % len(_SERIES_COLORS)]
        fw = float(row.get("frequency_weighted_egoscore", 0) or 0)
        row["_fw_pct"] = max(0.0, min(100.0, fw * 10.0))
        row["_chart_key"] = f"run-{idx}"
        row["_display_label"] = _run_display_label(row, duplicates=(run_counts.get(row["model"], 0) > 1))
        row["_judges_label"] = format_judges(row)

    details = _load_details(paths)
    benchmark_hash, task_count = _benchmark_meta(paths)
    categories = sorted({category for row in runs for category in row.get("per_category", {})})
    model_options = sorted({row["model"] for row in runs})
    # When every run shares one judging panel, surface it once in the header
    # instead of repeating an identical label down the column.
    judge_labels = {row["_judges_label"] for row in runs}
    shared_judges_label = next(iter(judge_labels)) if len(judge_labels) == 1 and "—" not in judge_labels else ""

    top = runs[0] if runs else None
    top_score = float(top.get("frequency_weighted_egoscore", 0)) if top else None
    total_cost = sum(float(row.get("run_cost_usd", 0) or 0) for row in runs)
    total_wall = sum(float(row.get("wall_time_seconds", 0) or 0) for row in runs)

    charts = {
        "__all__": {
            "bars": bar_svg(_combined_per_category(runs)),
        }
    }
    for row in runs:
        per_cat = row.get("per_category", {})
        charts[row["_chart_key"]] = {
            "bars": bar_svg(per_cat),
        }

    html = HTML_TEMPLATE.render(
        runs=runs,
        details=details,
        categories=categories,
        benchmark_hash_short=(benchmark_hash[:12] if benchmark_hash else ""),
        task_count=task_count,
        run_count=len(runs),
        top_model_name=(top["model"] if top else None),
        top_score=top_score,
        total_cost=total_cost,
        total_wall=total_wall,
        bars_all=charts["__all__"]["bars"],
        charts_json=json.dumps(charts),
        model_options=model_options,
        shared_judges_label=shared_judges_label,
    )
    paths.report_html.write_text(html, encoding="utf-8")
    paths.report_md.write_text(render_markdown(paths), encoding="utf-8")


def _combined_per_category(runs: list[dict]) -> dict[str, float]:
    totals: dict[str, list[float]] = {}
    for row in runs:
        for category, value in row.get("per_category", {}).items():
            totals.setdefault(category, []).append(float(value))
    return {category: sum(values) / len(values) for category, values in totals.items()}


def _run_counts_by_model(runs: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in runs:
        model = str(row.get("model", ""))
        counts[model] = counts.get(model, 0) + 1
    return counts


def _run_display_label(row: dict, *, duplicates: bool) -> str:
    model = str(row.get("model", ""))
    if not duplicates:
        return model
    run_name = Path(str(row.get("_path", ""))).name.strip()
    if run_name:
        return f"{model} · {run_name}"
    created_at = str(row.get("created_at", "")).strip()
    if created_at:
        return f"{model} · {created_at}"
    return model


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
            rationale_row = rationales.get(task_id, {})
            prompt = task.get("prompt", "")
            checklist = task.get("checklist", []) or []
            score_num = _safe_float(score)
            judges, judge_text = _judge_details(rationale_row)
            legacy_rationale = str(rationale_row.get("rationale", ""))
            search = " ".join([model, task_id, prompt, response, judge_text or legacy_rationale]).lower()
            items.append(
                {
                    "model": model,
                    "task_id": task_id,
                    "prompt": prompt,
                    "prompt_preview": _preview(prompt),
                    "checklist": checklist,
                    "response": response or "—",
                    "score": score,
                    "score_num": score_num if score_num is not None else "",
                    "score_display": _score_display(score_num, score),
                    "score_tier_class": _score_tier_class(score_num),
                    "rationale": legacy_rationale or "—",
                    "judges": judges,
                    "judge_spread": rationale_row.get("judge_spread"),
                    "search": search,
                }
            )
    return items


def _judge_details(rationale_row: dict) -> tuple[list[dict], str]:
    """Normalize a rationale row into per-judge display blocks + search text.

    New panel runs carry a ``judges`` list; legacy runs have a single top-level
    rationale and return an empty list (the template falls back to it).
    """
    judges: list[dict] = []
    texts: list[str] = []
    for entry in rationale_row.get("judges") or []:
        if not isinstance(entry, dict):
            continue
        score_num = _safe_float(entry.get("score"))
        rationale = str(entry.get("rationale") or "")
        texts.append(rationale)
        judges.append(
            {
                "judge": str(entry.get("judge") or "judge"),
                "score_display": _score_display(score_num, entry.get("score")),
                "score_tier_class": _score_tier_class(score_num),
                "rationale": rationale or "—",
            }
        )
    return judges, " ".join(texts)


def _jsonl_by_id(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    rows: dict[str, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        rows[payload["task_id"]] = payload
    return rows


def _safe_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _score_display(score_num: float | None, raw: object) -> str:
    if score_num is None:
        return str(raw) if raw not in (None, "") else "—"
    if float(score_num).is_integer():
        return f"{int(score_num)}"
    return f"{score_num:.2f}"


def _score_tier_class(score_num: float | None) -> str:
    if score_num is None:
        return "badge-model"
    if score_num >= 8:
        return "badge-score-high"
    if score_num >= 4:
        return "badge-score-mid"
    return "badge-score-low"


def _preview(text: str, limit: int = 140) -> str:
    if not text:
        return "—"
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"
