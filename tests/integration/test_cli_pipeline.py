import json
from pathlib import Path

from typer.testing import CliRunner

from egobench.cli import app


FIXTURES = Path(__file__).parents[1] / "fixtures"


def test_cli_build_eval_report_reproducible(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    runner = CliRunner()

    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0, result.output

    result = runner.invoke(
        app,
        ["ingest", str(FIXTURES / "chatgpt_export_sample.json"), "--adapter", "chatgpt"],
    )
    assert result.exit_code == 0, result.output
    assert "phase1: resolving adapter" in result.output
    assert "phase1: loading export with chatgpt adapter" in result.output
    assert "phase1: importing 10 conversations" in result.output

    result = runner.invoke(app, ["build", "--yes"])
    assert result.exit_code == 0, result.output
    assert "Build models" in result.output
    assert "phase2 filter" in result.output
    assert "phase3 embeddings" in result.output
    assert "recorded fallback" in result.output
    assert "deterministic fallback" in result.output
    assert "Phase 2/8: filter non-task conversations (phase2)" in result.output
    assert "phase2: scanning 10 conversations" in result.output
    assert "phase3: embedding and clustering 8 tasks" in result.output
    assert "Completed phase3" in result.output
    assert "phase8: locking 8 benchmark tasks" in result.output
    benchmark_path = tmp_path / "egobench-workspace" / "benchmark.json"
    first = benchmark_path.read_text()
    assert "benchmark_hash" in first

    result = runner.invoke(app, ["build", "--from", "3", "--yes"])
    assert result.exit_code == 0, result.output
    assert "Skipping phase2 (filter non-task conversations); cache key matched." in result.output
    assert "Phase 3/8: embed and cluster task candidates (phase3)" in result.output
    assert "Completed phase8" in result.output
    second = benchmark_path.read_text()
    assert '"task_count": 8' in second
    benchmark = _json_from_text(second)
    assert benchmark["metadata"]["task_family_count"] >= 1
    assert benchmark["metadata"]["family_distribution"]
    assert benchmark["metadata"]["domain_distribution"]
    assert benchmark["metadata"]["difficulty_distribution"]
    assert benchmark["metadata"]["specificity_distribution"]
    task = benchmark["tasks"][0]
    assert task["task_family_id"]
    assert task["task_family"]
    assert task["domain"]
    assert isinstance(task["skills"], list)
    assert task["difficulty"] in {"easy", "medium", "hard"}
    assert task["specificity"] in {"generalizable", "narrow", "one_off"}

    result = runner.invoke(app, ["build", "--from", "7", "--yes"])
    assert result.exit_code == 0, result.output
    assert "Skipping phase4" in result.output
    assert "Phase 7/8: draft and merge task checklists (phase7)" in result.output
    assert "Phase 4/8" not in result.output

    first_hash = _hash_from_text(first)
    second_hash = _hash_from_text(second)
    assert first_hash == second_hash

    # OPENAI_API_KEY is unset, so the recorded fallback handles candidate calls.
    result = runner.invoke(
        app,
        ["eval", "--model", "openai/gpt-5", "--yes"],
    )
    assert result.exit_code == 0, result.output
    assert "eval: running 8 tasks with openai:gpt-5" in result.output
    assert "eval: [1/8] answering task-0001" in result.output
    assert "eval: completed 8 tasks" in result.output

    # Panel scoring: repeat --judge to score every answer with multiple judges
    # and aggregate per task. Recorded fallback handles all calls offline.
    result = runner.invoke(
        app,
        [
            "eval", "--model", "openai/gpt-5",
            "--judge", "anthropic/claude-opus-4-7", "--judge", "openai/gpt-5", "--yes",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "2-judge panel" in result.output
    runs_dir = tmp_path / "egobench-workspace" / "runs"
    score_rows = [
        json.loads(line)
        for path in runs_dir.glob("*/*/scores.jsonl")
        for line in path.read_text().splitlines()
        if line.strip()
    ]
    assert any(len(row.get("judge_scores", {})) == 2 for row in score_rows)

    result = runner.invoke(app, ["report"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "egobench-workspace" / "report.html").exists()
    assert (tmp_path / "egobench-workspace" / "report.md").exists()

    result = runner.invoke(app, ["leaderboard"])
    assert result.exit_code == 0, result.output
    assert "openai:gpt-5" in result.output


def test_cli_status_help_and_expected_errors(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    runner = CliRunner()

    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0, result.output
    assert "EgoBench Status" in result.output
    assert "Workspace" in result.output
    assert "missing" in result.output
    assert "Next: `egobench init`" in result.output

    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0, result.output
    assert "Environment: shell only; no .env at" in result.output
    assert "Next: add API keys to .env if needed" in result.output

    result = runner.invoke(app, ["build", "--yes"])
    assert result.exit_code == 1, result.output
    assert "No conversations found." in result.output
    assert "Traceback" not in result.output

    result = runner.invoke(app, ["eval", "--model", "openai/gpt-5", "--dry-run"])
    assert result.exit_code == 1, result.output
    assert "No benchmark.json found." in result.output
    assert "Traceback" not in result.output

    result = runner.invoke(app, ["eval", "--provider", "openai", "--model", "gpt-5", "--dry-run"])
    assert result.exit_code != 0, result.output
    assert "No such option" in result.output

    result = runner.invoke(app, ["ingest", "--help"])
    assert result.exit_code == 0, result.output
    assert "[auto|chatgpt|claude|jsonl]" in result.output

    result = runner.invoke(app, ["eval", "--help"])
    assert result.exit_code == 0, result.output
    assert "provider/model-id" in result.output
    assert "--dry-run" in result.output
    assert "key in )." not in result.output

    result = runner.invoke(app, ["review", "--help"])
    assert result.exit_code == 0, result.output
    assert "--port" not in result.output


def _hash_from_text(text: str) -> str:
    return _json_from_text(text)["metadata"]["benchmark_hash"]


def _json_from_text(text: str) -> dict:
    import json

    return json.loads(text)
