from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import keyring


DEFAULT_CONFIG_TEXT = """# egobench-workspace/egobench.toml
#
# Every model in EgoBench is referenced as { provider, model }. Providers are
# declared once below; everything else just points at a provider by name and
# supplies the bare model id. There is no string-prefix magic — if you want to
# use a model, its provider must appear in [providers.*] first.
#
# Every provider speaks the OpenAI chat-completions protocol. Anthropic ships
# its own openai-compatible endpoint, so Claude models go through the same
# client as everything else.
#
# Provider fields:
#   api_key_env  Optional. Name of the env var to read the API key from. Omit
#                for local servers that ignore auth (LM Studio, Ollama). If
#                you declare it but the env var is missing at runtime, EgoBench
#                falls back to a deterministic recorded client — handy for
#                offline tests, but you will not be hitting the real model.
#   base_url     Optional for `openai` (defaults to api.openai.com). Required
#                for every other provider, since they live at different URLs.

[workspace]
# Controls deterministic choices during `egobench build`, especially phase 6
# sampling. Keep the same seed to recreate the same benchmark from the same
# inputs and config; change it to reshuffle which eligible tasks are selected.
seed = 42

[providers.anthropic]
api_key_env = "ANTHROPIC_API_KEY"
base_url = "https://api.anthropic.com/v1/"

[providers.openai]
api_key_env = "OPENAI_API_KEY"
# base_url is omitted on purpose: the OpenAI SDK defaults to api.openai.com.

# --- Optional providers (uncomment to use) ---------------------------------
#
# OpenRouter and other OpenAI-compatible gateways are just more providers.
# Reference models by their gateway-specific id, e.g. "anthropic/claude-opus-4".
#
# [providers.openrouter]
# api_key_env = "OPENROUTER_API_KEY"
# base_url = "https://openrouter.ai/api/v1"
#
# Local OpenAI-compatible servers ignore auth, so no api_key_env is needed.
#
# [providers.lmstudio]
# base_url = "http://localhost:1234/v1"
#
# [providers.ollama]
# base_url = "http://localhost:11434/v1"

# --- Judges ----------------------------------------------------------------
#
# A "judge" is a model EgoBench uses to do its own internal work — labeling
# clusters during `build`, writing the rubric (checklist) for each task, and
# scoring answers during `eval`. Judges are separate from the model you are
# benchmarking; you usually want a strong, expensive judge that scores fairly
# regardless of which model is being tested.
#
# [judges.default] is the single judge used for `eval` scoring and the
# checklist-merge step. [[judges.checklist_panel]] is an array of models used
# to draft rubric items during phase 7 — using a panel (rather than one model)
# reduces single-model bias in what counts as "doing well" on a task. The
# default judge then merges the panel's items into the final rubric.
#
# Add one [[judges.checklist_panel]] block per panel model. You can include as
# many as you want; each block must name a declared provider and the model id to
# call through that provider.

[judges.default]
provider = "anthropic"
model = "claude-opus-4-7"

[[judges.checklist_panel]]
provider = "anthropic"
model = "claude-opus-4-7"

[[judges.checklist_panel]]
provider = "openai"
model = "gpt-5"

# --- Embeddings ------------------------------------------------------------
#
# `egobench build` embeds your conversations to cluster them by topic. The
# default routes through OpenAI; to keep embeddings local, point this at any
# OpenAI-compatible embeddings server (LM Studio is the simplest path — see
# README "Local / self-hosted embeddings"):
#
# [embeddings]
# provider = "lmstudio"
# model = "nomic-embed-text-v1.5"

[embeddings]
provider = "openai"
model = "text-embedding-3-small"

# --- Sampling --------------------------------------------------------------
#
# target_n         Number of tasks to keep in the final benchmark (1–200).
# oversample_alpha In (0, 1]. Lower values give rare categories more weight,
#                  preventing one giant cluster from drowning out the long
#                  tail. 1.0 = strict proportional sampling.

[sample]
target_n = 100
oversample_alpha = 0.8
"""


class ConfigError(ValueError):
    """Raised when egobench.toml is malformed or references unknown providers."""


@dataclass(frozen=True)
class ProviderCfg:
    name: str
    api_key_env: str | None = None
    api_key_keyring: str | None = None
    base_url: str | None = None


@dataclass(frozen=True)
class ModelRef:
    provider: str
    model: str
    base_url: str | None = None

    def display(self) -> str:
        return f"{self.provider}:{self.model}"


@dataclass(frozen=True)
class WorkspaceCfg:
    seed: int = 42


@dataclass(frozen=True)
class JudgesCfg:
    default: ModelRef
    checklist_panel: list[ModelRef] = field(default_factory=list)


@dataclass(frozen=True)
class EmbeddingsCfg:
    provider: str = "openai"
    model: str = "text-embedding-3-small"


@dataclass(frozen=True)
class SampleCfg:
    target_n: int = 100
    oversample_alpha: float = 0.8


@dataclass(frozen=True)
class EgoBenchConfig:
    workspace: WorkspaceCfg
    providers: dict[str, ProviderCfg]
    judges: JudgesCfg
    embeddings: EmbeddingsCfg
    sample: SampleCfg

    def provider(self, name: str) -> ProviderCfg:
        if name not in self.providers:
            raise ConfigError(
                f"Unknown provider '{name}'. Declare it under [providers.{name}] in egobench.toml."
            )
        return self.providers[name]

    def api_key_for_provider(self, name: str) -> str | None:
        cfg = self.provider(name)
        if cfg.api_key_env:
            value = os.environ.get(cfg.api_key_env)
            if value:
                return value
        if cfg.api_key_keyring:
            service, _, account = cfg.api_key_keyring.partition("/")
            if service and account:
                stored = keyring.get_password(service, account)
                if stored:
                    return stored
        return None

    def api_key_for(self, ref: ModelRef) -> str | None:
        return self.api_key_for_provider(ref.provider)


def load_config(path: Path) -> EgoBenchConfig:
    if not path.exists():
        return parse_config(tomllib.loads(DEFAULT_CONFIG_TEXT))
    return parse_config(tomllib.loads(path.read_text(encoding="utf-8")))


def parse_config(raw: dict[str, Any]) -> EgoBenchConfig:
    providers = _parse_providers(raw.get("providers", {}))
    judges = _parse_judges(raw.get("judges"), providers)
    embeddings = _parse_embeddings(raw.get("embeddings"), providers)

    workspace_raw = raw.get("workspace", {})
    sample_raw = raw.get("sample", {})
    target_n = max(1, min(200, int(sample_raw.get("target_n", 100))))

    return EgoBenchConfig(
        workspace=WorkspaceCfg(seed=int(workspace_raw.get("seed", 42))),
        providers=providers,
        judges=judges,
        embeddings=embeddings,
        sample=SampleCfg(
            target_n=target_n,
            oversample_alpha=float(sample_raw.get("oversample_alpha", 0.8)),
        ),
    )


def _parse_providers(raw: dict[str, Any]) -> dict[str, ProviderCfg]:
    out: dict[str, ProviderCfg] = {}
    for name, values in raw.items():
        if not isinstance(values, dict):
            raise ConfigError(f"[providers.{name}] must be a table.")
        if "kind" in values:
            raise ConfigError(
                f"[providers.{name}].kind is no longer supported. Every provider speaks "
                "OpenAI chat-completions; for Anthropic, set "
                'base_url = "https://api.anthropic.com/v1/".'
            )
        api_key_env = values.get("api_key_env")
        out[name] = ProviderCfg(
            name=name,
            api_key_env=str(api_key_env).strip() if api_key_env else None,
            api_key_keyring=values.get("api_key_keyring"),
            base_url=values.get("base_url"),
        )
    return out


def _parse_model_ref(raw: Any, providers: dict[str, ProviderCfg], where: str) -> ModelRef:
    if not isinstance(raw, dict):
        raise ConfigError(
            f"{where} must be a table with `provider` and `model` keys "
            f"(got {type(raw).__name__}). Bare model strings are no longer supported — "
            "see README 'Choosing which APIs get called'."
        )
    provider = raw.get("provider")
    model = raw.get("model")
    if not provider or not model:
        raise ConfigError(f"{where} requires both `provider` and `model`.")
    provider = str(provider)
    if provider not in providers:
        raise ConfigError(
            f"{where}: provider '{provider}' is not declared under [providers.{provider}]."
        )
    base_url = raw.get("base_url")
    return ModelRef(provider=provider, model=str(model), base_url=base_url)


def _parse_judges(raw: Any, providers: dict[str, ProviderCfg]) -> JudgesCfg:
    if raw is None:
        raise ConfigError("Missing [judges] section in egobench.toml.")
    if not isinstance(raw, dict):
        raise ConfigError("[judges] must be a table.")
    default = _parse_model_ref(raw.get("default"), providers, "[judges.default]")
    panel_raw = raw.get("checklist_panel", [])
    if not isinstance(panel_raw, list):
        raise ConfigError("[judges.checklist_panel] must be an array of tables.")
    panel = [
        _parse_model_ref(entry, providers, f"[judges.checklist_panel][{idx}]")
        for idx, entry in enumerate(panel_raw)
    ]
    return JudgesCfg(default=default, checklist_panel=panel)


def _parse_embeddings(raw: Any, providers: dict[str, ProviderCfg]) -> EmbeddingsCfg:
    if raw is None:
        return EmbeddingsCfg()
    if not isinstance(raw, dict):
        raise ConfigError("[embeddings] must be a table.")
    if "backend" in raw:
        raise ConfigError(
            "[embeddings].backend is no longer supported. Use `provider = \"<name>\"` "
            "pointing at a [providers.<name>] block."
        )
    if "local" in raw:
        raise ConfigError(
            "[embeddings.local] (in-process sentence-transformers) is no longer "
            "supported. Use an OpenAI-compatible embeddings server like LM Studio "
            "or Ollama — see README 'Local / self-hosted embeddings'."
        )
    provider = raw.get("provider", "openai")
    model = raw.get("model", "text-embedding-3-small")
    if provider not in providers:
        raise ConfigError(
            f"[embeddings].provider = '{provider}' is not declared under [providers.{provider}]."
        )
    return EmbeddingsCfg(provider=str(provider), model=str(model))


def stable_config_dict(cfg: EgoBenchConfig) -> dict[str, Any]:
    def _ref(ref: ModelRef) -> dict[str, str]:
        return {"provider": ref.provider, "model": ref.model}

    return {
        "workspace": {"seed": cfg.workspace.seed},
        "judges": {
            "default": _ref(cfg.judges.default),
            "checklist_panel": [_ref(ref) for ref in cfg.judges.checklist_panel],
        },
        "embeddings": {"provider": cfg.embeddings.provider, "model": cfg.embeddings.model},
        "sample": {"target_n": cfg.sample.target_n, "oversample_alpha": cfg.sample.oversample_alpha},
    }
