import json

from egobench.paths import WorkspacePaths
from egobench.reporting.html import render_reports


def test_html_report_guards_layout_against_long_model_names(tmp_path):
    paths = WorkspacePaths(tmp_path)
    run_dir = paths.runs_dir / "openrouter" / "run-1"
    run_dir.mkdir(parents=True)
    paths.benchmark.write_text(
        json.dumps(
            {
                "metadata": {
                    "benchmark_hash": "59bd40c1b852",
                    "task_count": 97,
                },
                "tasks": [],
            }
        ),
        encoding="utf-8",
    )
    long_model = "openrouter:anthropic/claude-sonnet-4.6-extra-long-context-model"
    (run_dir / "summary.json").write_text(
        json.dumps(
            {
                "model": long_model,
                "raw_egoscore": 6.12,
                "frequency_weighted_egoscore": 6.74,
                "run_cost_usd": 0,
                "wall_time_seconds": 14035,
                "per_category": {},
            }
        ),
        encoding="utf-8",
    )

    render_reports(paths)

    html = paths.report_html.read_text(encoding="utf-8")
    assert long_model in html
    assert "grid-template-columns: minmax(0, 2fr) repeat(3, minmax(0, 1fr));" in html
    assert "overflow-wrap: anywhere;" in html
    assert "min-width: 1120px;" in html
    assert "table-layout: fixed;" in html


def test_html_report_omits_unreadable_radar_chart(tmp_path):
    paths = WorkspacePaths(tmp_path)
    run_dir = paths.runs_dir / "openrouter" / "run-1"
    run_dir.mkdir(parents=True)
    paths.benchmark.write_text(
        json.dumps({"metadata": {"benchmark_hash": "abc", "task_count": 2}, "tasks": []}),
        encoding="utf-8",
    )
    (run_dir / "summary.json").write_text(
        json.dumps(
            {
                "model": "openrouter:test/model",
                "raw_egoscore": 6.0,
                "frequency_weighted_egoscore": 6.0,
                "run_cost_usd": 0,
                "wall_time_seconds": 1,
                "per_category": {
                    "creative ideation and campaign planning": 7.0,
                    "customer discovery interviewing": 5.0,
                },
            }
        ),
        encoding="utf-8",
    )

    render_reports(paths)

    html = paths.report_html.read_text(encoding="utf-8")
    assert "Per-category breakdown" in html
    assert "chart-bars" in html
    assert "Per-category bar chart" in html
    assert "chart-radar" not in html
    assert "ebx-radar" not in html
    assert "Per-category radar chart" not in html


def test_html_report_escapes_model_response_content(tmp_path):
    paths = WorkspacePaths(tmp_path)
    run_dir = paths.runs_dir / "openrouter" / "run-1"
    run_dir.mkdir(parents=True)
    paths.benchmark.write_text(
        json.dumps(
            {
                "metadata": {"benchmark_hash": "abc", "task_count": 1},
                "tasks": [
                    {
                        "id": "t1",
                        "prompt": "hi",
                        "checklist": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "summary.json").write_text(
        json.dumps(
            {
                "model": "openrouter:test/model",
                "raw_egoscore": 5.0,
                "frequency_weighted_egoscore": 5.0,
                "run_cost_usd": 0,
                "wall_time_seconds": 1,
                "per_category": {},
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "tasks.jsonl").write_text(
        json.dumps({"task_id": "t1", "prompt": "hi", "checklist": []}) + "\n",
        encoding="utf-8",
    )
    injected = "<style>body{max-width:1px}</style><script>alert(1)</script>"
    (run_dir / "responses.jsonl").write_text(
        json.dumps({"task_id": "t1", "response": injected}) + "\n",
        encoding="utf-8",
    )

    render_reports(paths)

    html = paths.report_html.read_text(encoding="utf-8")
    assert injected not in html
    assert "&lt;style&gt;" in html
    assert "&lt;script&gt;" in html


def test_html_report_shows_judges_for_each_run(tmp_path):
    paths = WorkspacePaths(tmp_path)
    run_dir = paths.runs_dir / "openai_gpt-5" / "run-1"
    run_dir.mkdir(parents=True)
    paths.benchmark.write_text(
        json.dumps({"metadata": {"benchmark_hash": "abc", "task_count": 1}, "tasks": []}),
        encoding="utf-8",
    )
    (run_dir / "summary.json").write_text(
        json.dumps(
            {
                "model": "openai:gpt-5",
                "raw_egoscore": 7.0,
                "frequency_weighted_egoscore": 7.0,
                "run_cost_usd": 0,
                "wall_time_seconds": 1,
                "per_category": {},
                "judges": ["anthropic:claude-opus-4-7", "openai:gpt-5"],
                "scoring_aggregate": "median",
            }
        ),
        encoding="utf-8",
    )

    render_reports(paths)

    html = paths.report_html.read_text(encoding="utf-8")
    # Column header and the panel label (with aggregate suffix) both render.
    assert "Judged by" in html
    assert "anthropic:claude-opus-4-7, openai:gpt-5 · median" in html
    # A single shared panel is also surfaced once in the section header.
    assert "judged by anthropic:claude-opus-4-7, openai:gpt-5 · median" in html


def test_html_report_judges_fall_back_for_legacy_runs(tmp_path):
    paths = WorkspacePaths(tmp_path)
    run_dir = paths.runs_dir / "legacy" / "run-1"
    run_dir.mkdir(parents=True)
    paths.benchmark.write_text(
        json.dumps({"metadata": {"benchmark_hash": "abc", "task_count": 1}, "tasks": []}),
        encoding="utf-8",
    )
    (run_dir / "summary.json").write_text(
        json.dumps(
            {
                "model": "legacy:model",
                "raw_egoscore": 5.0,
                "frequency_weighted_egoscore": 5.0,
                "run_cost_usd": 0,
                "wall_time_seconds": 1,
                "per_category": {},
            }
        ),
        encoding="utf-8",
    )

    render_reports(paths)

    html = paths.report_html.read_text(encoding="utf-8")
    assert "Judged by" in html
    # No judges recorded → em dash, and no spurious shared-panel header note.
    assert "judged by" not in html


def test_html_report_does_not_claim_shared_judges_when_legacy_runs_are_present(tmp_path):
    paths = WorkspacePaths(tmp_path)
    paths.benchmark.write_text(
        json.dumps({"metadata": {"benchmark_hash": "abc", "task_count": 2}, "tasks": []}),
        encoding="utf-8",
    )

    judged_run_dir = paths.runs_dir / "openai_gpt-5" / "run-1"
    judged_run_dir.mkdir(parents=True)
    (judged_run_dir / "summary.json").write_text(
        json.dumps(
            {
                "model": "openai:gpt-5",
                "raw_egoscore": 7.0,
                "frequency_weighted_egoscore": 7.0,
                "run_cost_usd": 0,
                "wall_time_seconds": 1,
                "per_category": {},
                "judges": ["anthropic:claude-opus-4-7", "openai:gpt-5"],
                "scoring_aggregate": "median",
            }
        ),
        encoding="utf-8",
    )

    legacy_run_dir = paths.runs_dir / "legacy" / "run-1"
    legacy_run_dir.mkdir(parents=True)
    (legacy_run_dir / "summary.json").write_text(
        json.dumps(
            {
                "model": "legacy:model",
                "raw_egoscore": 5.0,
                "frequency_weighted_egoscore": 5.0,
                "run_cost_usd": 0,
                "wall_time_seconds": 1,
                "per_category": {},
            }
        ),
        encoding="utf-8",
    )

    render_reports(paths)

    html = paths.report_html.read_text(encoding="utf-8")
    assert "Judged by" in html
    assert "anthropic:claude-opus-4-7, openai:gpt-5 · median" in html
    assert "judged by anthropic:claude-opus-4-7, openai:gpt-5 · median" not in html
