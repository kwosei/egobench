from egobench.config import EgoBenchConfig
from egobench.cost.estimator import build_estimate, eval_estimate


def test_default_estimates_stay_under_budget():
    cfg = EgoBenchConfig()
    assert sum(line.cost_usd for line in build_estimate(cfg, 500)) < 10
    assert sum(line.cost_usd for line in eval_estimate(cfg, "gpt-5", 100)) < 5

