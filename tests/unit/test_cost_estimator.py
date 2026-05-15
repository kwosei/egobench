from egobench.config import ModelRef, load_config
from egobench.cost.estimator import build_estimate, eval_estimate


def test_default_estimates_stay_under_budget(tmp_path):
    cfg = load_config(tmp_path / "missing.toml")  # falls back to DEFAULT_CONFIG_TEXT
    assert sum(line.cost_usd for line in build_estimate(cfg, 500)) < 10
    candidate = ModelRef(provider="openai", model="gpt-5")
    assert sum(line.cost_usd for line in eval_estimate(cfg, candidate, 100)) < 5
