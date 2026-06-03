from __future__ import annotations

import pytest
import typer

import egobench.eval.judge as judge_mod
from egobench.cli import _parse_cli_model_ref, _resolve_judge_panel
from egobench.config import ConfigError, ModelRef, ProviderCfg, parse_config
from egobench.eval.judge import aggregate_scores, judge_response_panel


# --- aggregate_scores -------------------------------------------------------


def test_aggregate_mean():
    assert aggregate_scores([8, 6], "mean") == 7.0
    assert aggregate_scores([9], "mean") == 9.0


def test_aggregate_median_odd_and_even():
    assert aggregate_scores([3, 7, 9], "median") == 7.0  # ignores the low outlier
    assert aggregate_scores([4, 8], "median") == 6.0


def test_aggregate_empty_is_neutral():
    assert aggregate_scores([], "mean") == 5.0
    assert aggregate_scores([], "median") == 5.0


# --- judge_response_panel ---------------------------------------------------


def _scripted(mapping: dict[str, dict]):
    def fake(*, db, cfg, judge_model, task_prompt, checklist, response):
        return mapping[judge_model.display()]

    return fake


def test_panel_drops_failed_judge_and_aggregates(monkeypatch):
    mapping = {
        "a:m": {"score": 8, "strengths": ["s"], "weaknesses": [], "rationale": "good", "ok": True},
        "b:m": {"score": 6, "strengths": [], "weaknesses": ["w"], "rationale": "ok", "ok": True},
        "c:m": {"score": 5, "strengths": [], "weaknesses": ["unparsed"], "rationale": "", "ok": False},
    }
    monkeypatch.setattr(judge_mod, "judge_response", _scripted(mapping))
    panel = [ModelRef("a", "m"), ModelRef("b", "m"), ModelRef("c", "m")]

    result = judge_response_panel(
        db=None, cfg=None, judge_models=panel,
        task_prompt="t", checklist=[], response="r", aggregate="mean",
    )

    assert result["score"] == 7.0  # mean of the two parseable judges; failed one dropped
    assert result["judge_scores"] == {"a:m": 8, "b:m": 6, "c:m": 5}
    assert result["judge_spread"] == 2  # max - min over contributing judges only
    assert result["aggregate"] == "mean"
    # Per-judge detail is preserved in submission order, including the failure.
    assert [j["judge"] for j in result["judges"]] == ["a:m", "b:m", "c:m"]
    assert [j["ok"] for j in result["judges"]] == [True, True, False]


def test_panel_all_failed_falls_back_to_neutral(monkeypatch):
    mapping = {
        "a:m": {"score": 5, "strengths": [], "weaknesses": ["x"], "rationale": "", "ok": False},
        "b:m": {"score": 5, "strengths": [], "weaknesses": ["x"], "rationale": "", "ok": False},
    }
    monkeypatch.setattr(judge_mod, "judge_response", _scripted(mapping))
    panel = [ModelRef("a", "m"), ModelRef("b", "m")]

    result = judge_response_panel(
        db=None, cfg=None, judge_models=panel,
        task_prompt="t", checklist=[], response="r", aggregate="mean",
    )

    assert result["score"] == 5.0
    assert result["judge_spread"] == 0


def test_single_judge_panel_is_identity(monkeypatch):
    mapping = {"a:m": {"score": 7, "strengths": [], "weaknesses": [], "rationale": "r", "ok": True}}
    monkeypatch.setattr(judge_mod, "judge_response", _scripted(mapping))

    result = judge_response_panel(
        db=None, cfg=None, judge_models=[ModelRef("a", "m")],
        task_prompt="t", checklist=[], response="r",
    )

    assert result["score"] == 7.0
    assert result["judge_spread"] == 0
    assert result["judge_scores"] == {"a:m": 7}


def test_panel_ignores_duplicate_judges(monkeypatch):
    mapping = {
        "a:m": {"score": 8, "strengths": [], "weaknesses": [], "rationale": "a", "ok": True},
        "b:m": {"score": 6, "strengths": [], "weaknesses": [], "rationale": "b", "ok": True},
    }
    monkeypatch.setattr(judge_mod, "judge_response", _scripted(mapping))

    result = judge_response_panel(
        db=None,
        cfg=None,
        judge_models=[ModelRef("a", "m"), ModelRef("a", "m"), ModelRef("b", "m")],
        task_prompt="t",
        checklist=[],
        response="r",
    )

    assert result["score"] == 7.0
    assert [j["judge"] for j in result["judges"]] == ["a:m", "b:m"]
    assert result["judge_scores"] == {"a:m": 8, "b:m": 6}


# --- config: scoring panel --------------------------------------------------


_BASE = {
    "providers": {
        "anthropic": {"api_key_env": "ANTHROPIC_API_KEY", "base_url": "https://api.anthropic.com/v1/"},
        "openai": {"api_key_env": "OPENAI_API_KEY"},
    },
    "judges": {"default": {"provider": "anthropic", "model": "claude-opus-4-7"}},
}


def test_scoring_panel_defaults_to_single_default_judge():
    cfg = parse_config(_BASE)
    assert cfg.judges.scoring_panel == []
    assert cfg.judges.scoring_aggregate == "mean"
    assert cfg.judges.exclude_candidate_provider is False
    assert cfg.judges.eval_judges() == [ModelRef("anthropic", "claude-opus-4-7")]


def test_scoring_panel_parsed_and_drives_eval_judges():
    raw = {
        **_BASE,
        "judges": {
            "default": {"provider": "anthropic", "model": "claude-opus-4-7"},
            "scoring_aggregate": "median",
            "exclude_candidate_provider": True,
            "scoring_panel": [
                {"provider": "anthropic", "model": "claude-opus-4-7"},
                {"provider": "openai", "model": "gpt-5"},
            ],
        },
    }
    cfg = parse_config(raw)
    assert [r.display() for r in cfg.judges.scoring_panel] == ["anthropic:claude-opus-4-7", "openai:gpt-5"]
    assert cfg.judges.scoring_aggregate == "median"
    assert cfg.judges.exclude_candidate_provider is True
    assert cfg.judges.eval_judges() == cfg.judges.scoring_panel


def test_resolve_judge_panel_deduplicates_explicit_and_config_judges():
    raw = {
        **_BASE,
        "judges": {
            "default": {"provider": "anthropic", "model": "claude-opus-4-7"},
            "scoring_panel": [
                {"provider": "anthropic", "model": "claude-opus-4-7"},
                {"provider": "anthropic", "model": "claude-opus-4-7"},
                {"provider": "openai", "model": "gpt-5"},
            ],
        },
    }
    cfg = parse_config(raw)
    candidate = ModelRef("candidate", "model")

    config_panel = _resolve_judge_panel(cfg, candidate, None)
    assert [ref.display() for ref in config_panel] == [
        "anthropic:claude-opus-4-7",
        "openai:gpt-5",
    ]

    explicit_panel = _resolve_judge_panel(
        cfg,
        candidate,
        ["openai/gpt-5", "openai/gpt-5", "anthropic/claude-opus-4-7"],
    )
    assert [ref.display() for ref in explicit_panel] == [
        "openai:gpt-5",
        "anthropic:claude-opus-4-7",
    ]


def test_cli_model_ref_parser_accepts_nested_gateway_ids():
    providers = {
        "anthropic": ProviderCfg(name="anthropic"),
        "openai": ProviderCfg(name="openai"),
        "openrouter": ProviderCfg(name="openrouter"),
    }

    assert _parse_cli_model_ref("anthropic/claude-opus-4-7", providers, "--model") == ModelRef(
        "anthropic", "claude-opus-4-7"
    )
    assert _parse_cli_model_ref("openrouter/anthropic/claude-opus-4-7", providers, "--judge") == ModelRef(
        "openrouter", "anthropic/claude-opus-4-7"
    )


@pytest.mark.parametrize("spec", ["gpt-5", "openai/", "/gpt-5", "openai:gpt-5"])
def test_cli_model_ref_parser_rejects_invalid_refs(spec):
    providers = {"openai": ProviderCfg(name="openai")}

    with pytest.raises(typer.BadParameter, match="provider/model-id"):
        _parse_cli_model_ref(spec, providers, "--model")


def test_cli_model_ref_parser_rejects_unknown_provider():
    providers = {"openai": ProviderCfg(name="openai")}

    with pytest.raises(typer.BadParameter, match="Unknown provider 'unknown'"):
        _parse_cli_model_ref("unknown/gpt-5", providers, "--model")


def test_invalid_scoring_aggregate_raises():
    raw = {
        **_BASE,
        "judges": {"default": {"provider": "anthropic", "model": "claude-opus-4-7"}, "scoring_aggregate": "average"},
    }
    with pytest.raises(ConfigError, match="mean.*median"):
        parse_config(raw)


def test_invalid_exclude_candidate_provider_type_raises():
    raw = {
        **_BASE,
        "judges": {
            "default": {"provider": "anthropic", "model": "claude-opus-4-7"},
            "exclude_candidate_provider": "false",
        },
    }
    with pytest.raises(ConfigError, match="true or false"):
        parse_config(raw)


def test_unknown_provider_in_scoring_panel_raises():
    raw = {
        **_BASE,
        "judges": {
            "default": {"provider": "anthropic", "model": "claude-opus-4-7"},
            "scoring_panel": [{"provider": "nope", "model": "x"}],
        },
    }
    with pytest.raises(ConfigError):
        parse_config(raw)
