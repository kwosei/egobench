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

    result = runner.invoke(app, ["build", "--yes"])
    assert result.exit_code == 0, result.output
    benchmark_path = tmp_path / "egobench-workspace" / "benchmark.json"
    first = benchmark_path.read_text()
    assert "benchmark_hash" in first

    result = runner.invoke(app, ["build", "--from", "3", "--yes"])
    assert result.exit_code == 0, result.output
    second = benchmark_path.read_text()
    assert '"task_count": 8' in second

    first_hash = _hash_from_text(first)
    second_hash = _hash_from_text(second)
    assert first_hash == second_hash

    # OPENAI_API_KEY is unset, so the recorded fallback handles candidate calls.
    result = runner.invoke(
        app,
        ["eval", "--provider", "openai", "--model", "gpt-5", "--yes"],
    )
    assert result.exit_code == 0, result.output

    result = runner.invoke(app, ["report"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "egobench-workspace" / "report.html").exists()
    assert (tmp_path / "egobench-workspace" / "report.md").exists()

    result = runner.invoke(app, ["leaderboard"])
    assert result.exit_code == 0, result.output
    assert "openai:gpt-5" in result.output


def _hash_from_text(text: str) -> str:
    import json

    return json.loads(text)["metadata"]["benchmark_hash"]
