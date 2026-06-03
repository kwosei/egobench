from dataclasses import replace

from rich.console import Console

from egobench.config import ModelRef, ProviderCfg, load_config, parse_config
from egobench.cost.estimator import (
    build_estimate,
    estimate_table,
    eval_estimate,
    has_approximate_prices,
    has_unknown_prices,
)
from egobench.llm.pricing import PricingResolver


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


def test_eval_estimate_marks_normalized_model_prices_as_approximate(tmp_path):
    cfg = load_config(tmp_path / "missing.toml")
    candidate = ModelRef(provider="openai", model="gpt-5-5")
    panel = [
        ModelRef(provider="anthropic", model="claude-opus-4-8"),
        ModelRef(provider="openai", model="gpt-5"),
    ]

    lines = eval_estimate(cfg, candidate, 100, panel)

    assert [line.price_known for line in lines] == [True, True, True]
    assert [line.price_label for line in lines] == ["normalized/builtin", "builtin", "builtin"]
    assert has_approximate_prices(lines)
    assert not has_unknown_prices(lines)

    console = Console(record=True, width=120)
    console.print(estimate_table(lines))
    output = console.export_text()
    assert "normalized/builtin" in output
    assert "≈$4.37" in output


def test_eval_estimate_uses_cached_public_catalog_prices(tmp_path):
    cfg = load_config(tmp_path / "missing.toml")
    pricing = PricingResolver(
        fetch_external=False,
        litellm_catalog={
            "gpt-5.5": {
                "input_cost_per_token": 0.000005,
                "output_cost_per_token": 0.00003,
            },
            "claude-opus-4-8": {
                "input_cost_per_token": 0.000005,
                "output_cost_per_token": 0.000025,
            },
            "gemini-3.1-pro-preview": {
                "input_cost_per_token": 0.000002,
                "output_cost_per_token": 0.000012,
            },
            "xai/grok-4.3": {
                "input_cost_per_token": 0.00000125,
                "output_cost_per_token": 0.0000025,
            },
        },
    )
    candidate = ModelRef(provider="anthropic", model="claude-opus-4-8")
    panel = [
        ModelRef(provider="openai", model="gpt-5-5"),
        ModelRef(provider="gemini", model="gemini-3.1-pro-preview"),
        ModelRef(provider="xai", model="grok-4.3"),
    ]

    lines = eval_estimate(cfg, candidate, 100, panel, pricing=pricing)

    assert [line.price_label for line in lines] == [
        "litellm",
        "normalized/litellm",
        "litellm",
        "litellm",
    ]
    assert round(sum(line.cost_usd for line in lines), 2) == 3.91


def test_eval_estimate_prefers_config_pricing_overrides():
    cfg = parse_config(
        {
            "providers": {"openai": {"api_key_env": "OPENAI_API_KEY"}},
            "judges": {"default": {"provider": "openai", "model": "gpt-5"}},
            "pricing": {
                "models": [
                    {
                        "provider": "openai",
                        "model": "gpt-5-5",
                        "input_per_1m": 10.0,
                        "output_per_1m": 20.0,
                    }
                ]
            },
        }
    )
    pricing = PricingResolver.from_config(cfg, fetch_external=False)

    line = eval_estimate(
        cfg,
        ModelRef(provider="openai", model="gpt-5-5"),
        1,
        [],
        pricing=pricing,
    )[0]

    assert line.price_label == "config"
    assert line.cost_usd == 0.023


def test_eval_estimate_treats_local_unknown_model_as_zero_cost(tmp_path):
    cfg = load_config(tmp_path / "missing.toml")
    local_candidate = ModelRef(provider="lmstudio", model="google/gemma-4-e4b")
    cfg = replace(
        cfg,
        providers={**cfg.providers, "lmstudio": ProviderCfg(name="lmstudio")},
    )

    lines = eval_estimate(cfg, local_candidate, 1, [])

    assert lines[0].price_known
    assert lines[0].cost_usd == 0.0
    assert not has_unknown_prices(lines)
