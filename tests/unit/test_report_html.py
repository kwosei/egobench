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
    assert "min-width: 900px;" in html
    assert "table-layout: fixed;" in html


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
