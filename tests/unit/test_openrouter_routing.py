from __future__ import annotations

import pytest

from egobench.config import ConfigError, ModelRef, parse_config
from egobench.llm.factory import make_client
from egobench.llm.pricing import price_for


PROVIDERS_TOML = {
    "providers": {
        "anthropic": {
            "api_key_env": "ANTHROPIC_API_KEY",
            "base_url": "https://api.anthropic.com/v1/",
        },
        "openai": {"api_key_env": "OPENAI_API_KEY"},
        "openrouter": {
            "api_key_env": "OPENROUTER_API_KEY",
            "base_url": "https://openrouter.ai/api/v1",
        },
        "lmstudio": {"base_url": "http://localhost:1234/v1"},
    },
    "judges": {
        "default": {"provider": "anthropic", "model": "claude-opus-4-7"},
        "checklist_panel": [
            {"provider": "anthropic", "model": "claude-opus-4-7"},
            {"provider": "openai", "model": "gpt-5"},
        ],
    },
}


def _cfg():
    return parse_config(PROVIDERS_TOML)


def test_parses_provider_blocks():
    cfg = _cfg()
    assert cfg.providers["anthropic"].base_url == "https://api.anthropic.com/v1/"
    assert cfg.providers["openrouter"].base_url == "https://openrouter.ai/api/v1"
    assert cfg.providers["lmstudio"].api_key_env is None


def test_judges_parse_as_model_refs():
    cfg = _cfg()
    assert cfg.judges.default == ModelRef(provider="anthropic", model="claude-opus-4-7")
    assert [r.provider for r in cfg.judges.checklist_panel] == ["anthropic", "openai"]


def test_unknown_provider_in_judge_raises():
    bad = {**PROVIDERS_TOML, "judges": {"default": {"provider": "nope", "model": "x"}}}
    with pytest.raises(ConfigError):
        parse_config(bad)


def test_legacy_string_judge_raises_with_hint():
    bad = {**PROVIDERS_TOML, "judges": {"default": "claude-opus-4-7"}}
    with pytest.raises(ConfigError, match="provider.*model"):
        parse_config(bad)


def test_legacy_kind_field_raises_with_hint():
    bad = {
        "providers": {"weird": {"kind": "anthropic"}},
        "judges": PROVIDERS_TOML["judges"],
    }
    with pytest.raises(ConfigError, match="kind is no longer supported"):
        parse_config(bad)


def test_legacy_embeddings_local_raises_with_hint():
    bad = {
        **PROVIDERS_TOML,
        "embeddings": {"local": {"model": "all-MiniLM-L6-v2"}},
    }
    with pytest.raises(ConfigError, match="LM Studio"):
        parse_config(bad)


def test_api_key_routes_through_provider(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cfg = _cfg()
    ref = ModelRef(provider="openrouter", model="anthropic/claude-opus-4")
    assert cfg.api_key_for(ref) == "or-test"
    bare = ModelRef(provider="anthropic", model="claude-opus-4-7")
    assert cfg.api_key_for(bare) is None


def test_openrouter_uses_openai_client(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test")
    cfg = _cfg()

    captured: dict = {}

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("egobench.llm.openai_client.OpenAI", FakeOpenAI)
    meter = make_client(
        ModelRef(provider="openrouter", model="anthropic/claude-opus-4"),
        cfg, db=None, phase="test",
    )
    inner = meter.client
    assert inner.__class__.__name__ == "OpenAIClient"
    assert inner.model == "anthropic/claude-opus-4"
    assert captured["api_key"] == "or-test"
    assert captured["base_url"] == "https://openrouter.ai/api/v1"


def test_anthropic_provider_uses_openai_compat_endpoint(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-test")
    cfg = _cfg()

    captured: dict = {}

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("egobench.llm.openai_client.OpenAI", FakeOpenAI)
    meter = make_client(
        ModelRef(provider="anthropic", model="claude-opus-4-7"),
        cfg, db=None, phase="test",
    )
    assert meter.client.__class__.__name__ == "OpenAIClient"
    assert captured["api_key"] == "ant-test"
    assert captured["base_url"] == "https://api.anthropic.com/v1/"


def test_lmstudio_uses_placeholder_key_no_env_required(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    cfg = _cfg()

    captured: dict = {}

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("egobench.llm.openai_client.OpenAI", FakeOpenAI)
    meter = make_client(
        ModelRef(provider="lmstudio", model="qwen2.5-coder-32b"),
        cfg, db=None, phase="test",
    )
    assert meter.client.__class__.__name__ == "OpenAIClient"
    assert captured["api_key"] == "not-needed"
    assert captured["base_url"] == "http://localhost:1234/v1"


def test_missing_env_var_falls_back_to_recorded(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cfg = _cfg()
    meter = make_client(
        ModelRef(provider="anthropic", model="claude-opus-4-7"),
        cfg, db=None, phase="test",
    )
    assert meter.client.__class__.__name__ == "RecordedLLMClient"


def test_pricing_strips_vendor_prefix():
    assert price_for("anthropic/claude-opus-4-7") == price_for("claude-opus-4-7")
    assert price_for("openai/gpt-5") == price_for("gpt-5")
