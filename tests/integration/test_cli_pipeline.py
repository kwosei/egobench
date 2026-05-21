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
    assert "Running phase2" in result.output
    assert "phase2: scanning 10 conversations" in result.output
    assert "phase3: embedding and clustering 8 tasks" in result.output
    assert "Completed phase3" in result.output
    assert "phase8: locking 8 benchmark tasks" in result.output
    benchmark_path = tmp_path / "egobench-workspace" / "benchmark.json"
    first = benchmark_path.read_text()
    assert "benchmark_hash" in first

    result = runner.invoke(app, ["build", "--from", "3", "--yes"])
    assert result.exit_code == 0, result.output
    assert "Skipping phase2; cache key matched." in result.output
    assert "Running phase3" in result.output
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
    assert "Running phase7" in result.output
    assert "Running phase4" not in result.output

    first_hash = _hash_from_text(first)
    second_hash = _hash_from_text(second)
    assert first_hash == second_hash

    # OPENAI_API_KEY is unset, so the recorded fallback handles candidate calls.
    result = runner.invoke(
        app,
        ["eval", "--provider", "openai", "--model", "gpt-5", "--yes"],
    )
    assert result.exit_code == 0, result.output
    assert "eval: running 8 tasks with openai:gpt-5" in result.output
    assert "eval: [1/8] answering task-0001" in result.output
    assert "eval: completed 8 tasks" in result.output

    result = runner.invoke(app, ["report"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "egobench-workspace" / "report.html").exists()
    assert (tmp_path / "egobench-workspace" / "report.md").exists()

    result = runner.invoke(app, ["leaderboard"])
    assert result.exit_code == 0, result.output
    assert "openai:gpt-5" in result.output


def _hash_from_text(text: str) -> str:
    return _json_from_text(text)["metadata"]["benchmark_hash"]


def _json_from_text(text: str) -> dict:
    import json

    return json.loads(text)
