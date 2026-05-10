from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import keyring


DEFAULT_CONFIG_TEXT = """[workspace]
seed = 42

[providers.anthropic]
api_key_env = "ANTHROPIC_API_KEY"

[providers.openai]
api_key_env = "OPENAI_API_KEY"

[judges]
default = "claude-opus-4-7"
checklist_panel = ["claude-opus-4-7", "gpt-5"]

[embeddings]
backend = "openai"
model = "text-embedding-3-small"

[sample]
target_n = 100
oversample_alpha = 0.8

[candidates]
defaults = ["claude-opus-4-7", "gpt-5"]
"""


@dataclass(frozen=True)
class ProviderCfg:
    api_key_env: str
    api_key_keyring: str | None = None


@dataclass(frozen=True)
class WorkspaceCfg:
    seed: int = 42


@dataclass(frozen=True)
class JudgesCfg:
    default: str = "claude-opus-4-7"
    checklist_panel: list[str] = field(default_factory=lambda: ["claude-opus-4-7", "gpt-5"])


@dataclass(frozen=True)
class EmbeddingsCfg:
    backend: str = "openai"
    model: str = "text-embedding-3-small"


@dataclass(frozen=True)
class SampleCfg:
    target_n: int = 100
    oversample_alpha: float = 0.8


@dataclass(frozen=True)
class CandidatesCfg:
    defaults: list[str] = field(default_factory=lambda: ["claude-opus-4-7", "gpt-5"])


@dataclass(frozen=True)
class EgoBenchConfig:
    workspace: WorkspaceCfg = field(default_factory=WorkspaceCfg)
    providers: dict[str, ProviderCfg] = field(default_factory=dict)
    judges: JudgesCfg = field(default_factory=JudgesCfg)
    embeddings: EmbeddingsCfg = field(default_factory=EmbeddingsCfg)
    sample: SampleCfg = field(default_factory=SampleCfg)
    candidates: CandidatesCfg = field(default_factory=CandidatesCfg)

    def model_provider(self, model: str) -> str | None:
        lower = model.lower()
        if lower.startswith(("claude", "anthropic")):
            return "anthropic"
        if lower.startswith(("gpt", "o1", "o3", "o4", "openai")):
            return "openai"
        return None

    def api_key_for_model(self, model: str) -> str | None:
        provider = self.model_provider(model)
        if provider is None:
            return None
        return self.api_key_for_provider(provider)

    def api_key_for_provider(self, provider: str) -> str | None:
        cfg = self.providers.get(provider)
        if cfg is None:
            return None
        value = os.environ.get(cfg.api_key_env)
        if value:
            return value
        if cfg.api_key_keyring:
            service, _, account = cfg.api_key_keyring.partition("/")
            if service and account:
                return keyring.get_password(service, account)
        return None


def write_default_config(path: Path) -> bool:
    if path.exists():
        return False
    path.write_text(DEFAULT_CONFIG_TEXT, encoding="utf-8")
    return True


def load_config(path: Path) -> EgoBenchConfig:
    if not path.exists():
        return EgoBenchConfig(
            providers={
                "anthropic": ProviderCfg(api_key_env="ANTHROPIC_API_KEY"),
                "openai": ProviderCfg(api_key_env="OPENAI_API_KEY"),
            }
        )
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    return parse_config(raw)


def parse_config(raw: dict[str, Any]) -> EgoBenchConfig:
    providers = {
        name: ProviderCfg(
            api_key_env=str(values.get("api_key_env", "")).strip(),
            api_key_keyring=values.get("api_key_keyring"),
        )
        for name, values in raw.get("providers", {}).items()
    }
    providers.setdefault("anthropic", ProviderCfg(api_key_env="ANTHROPIC_API_KEY"))
    providers.setdefault("openai", ProviderCfg(api_key_env="OPENAI_API_KEY"))

    workspace_raw = raw.get("workspace", {})
    judges_raw = raw.get("judges", {})
    embeddings_raw = raw.get("embeddings", {})
    sample_raw = raw.get("sample", {})
    candidates_raw = raw.get("candidates", {})

    target_n = int(sample_raw.get("target_n", 100))
    target_n = max(1, min(200, target_n))

    return EgoBenchConfig(
        workspace=WorkspaceCfg(seed=int(workspace_raw.get("seed", 42))),
        providers=providers,
        judges=JudgesCfg(
            default=str(judges_raw.get("default", "claude-opus-4-7")),
            checklist_panel=list(judges_raw.get("checklist_panel", ["claude-opus-4-7", "gpt-5"])),
        ),
        embeddings=EmbeddingsCfg(
            backend=str(embeddings_raw.get("backend", "openai")),
            model=str(embeddings_raw.get("model", "text-embedding-3-small")),
        ),
        sample=SampleCfg(
            target_n=target_n,
            oversample_alpha=float(sample_raw.get("oversample_alpha", 0.8)),
        ),
        candidates=CandidatesCfg(defaults=list(candidates_raw.get("defaults", ["claude-opus-4-7", "gpt-5"]))),
    )


def stable_config_dict(cfg: EgoBenchConfig) -> dict[str, Any]:
    return {
        "workspace": {"seed": cfg.workspace.seed},
        "judges": {"default": cfg.judges.default, "checklist_panel": cfg.judges.checklist_panel},
        "embeddings": {"backend": cfg.embeddings.backend, "model": cfg.embeddings.model},
        "sample": {"target_n": cfg.sample.target_n, "oversample_alpha": cfg.sample.oversample_alpha},
        "candidates": {"defaults": cfg.candidates.defaults},
    }

