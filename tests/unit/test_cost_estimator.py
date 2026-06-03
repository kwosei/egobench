from egobench.config import ModelRef, load_config
from egobench.cost.estimator import build_estimate, eval_estimate


def test_default_estimates_stay_under_budget(tmp_path):
    cfg = load_config(tmp_path / "missing.toml")  # falls back to DEFAULT_CONFIG_TEXT
    assert sum(line.cost_usd for line in build_estimate(cfg, 500)) < 10
    candidate = ModelRef(provider="openai", model="gpt-5")
    assert sum(line.cost_usd for line in eval_estimate(cfg, candidate, 100)) < 5


def test_eval_estimate_one_judge_line_per_panel_member(tmp_path):
    cfg = load_config(tmp_path / "missing.toml")
    candidate = ModelRef(provider="openai", model="gpt-5")
    panel = [
        ModelRef(provider="anthropic", model="claude-opus-4-7"),
        ModelRef(provider="openai", model="gpt-5"),
    ]

    judge_lines = [line for line in eval_estimate(cfg, candidate, 50, panel) if line.phase == "judge"]
    assert [line.model.display() for line in judge_lines] == [
        "anthropic:claude-opus-4-7",
        "openai:gpt-5",
    ]

    # No explicit panel → a single judge line resolved from config (back-compat).
    default_judge_lines = [line for line in eval_estimate(cfg, candidate, 50) if line.phase == "judge"]
    assert len(default_judge_lines) == 1


def test_build_estimate_uses_batch_counts(tmp_path):
    cfg = load_config(tmp_path / "missing.toml")

    lines = build_estimate(cfg, 18, candidate_group_sizes=[8, 1, 9], selected_count=12)
    by_phase = {line.phase: line for line in lines}

    assert "phase2" not in by_phase
    assert by_phase["phase3-embeddings"].calls == 1
    assert by_phase["phase4"].calls == 5  # ceil(8/8) + ceil(1/8) + ceil(9/8) + ceil(18/120)
    assert by_phase["phase7-checklist"].calls == 3
    assert by_phase["phase7-merge"].calls == 3
