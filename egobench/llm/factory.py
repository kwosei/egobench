from __future__ import annotations

from egobench.config import EgoBenchConfig, ModelRef
from egobench.cost.tracker import CostMeter
from egobench.db import DB
from egobench.llm.openai_client import OpenAIClient
from egobench.llm.pricing import PricingResolver
from egobench.llm.recorded import RecordedLLMClient


def make_client(
    ref: ModelRef,
    cfg: EgoBenchConfig,
    db: DB | None,
    phase: str,
    *,
    pricing: PricingResolver | None = None,
) -> CostMeter:
    provider_cfg = cfg.provider(ref.provider)
    base_url = ref.base_url or provider_cfg.base_url
    api_key = cfg.api_key_for(ref)

    if provider_cfg.api_key_env and not api_key:
        # Real provider declared an env var but it's missing — fall back to
        # the deterministic recorded client (offline tests / no-key smoke).
        client = RecordedLLMClient(ref=ref)
    else:
        # Local servers (no api_key_env) accept any string; the SDK requires one.
        client = OpenAIClient(model=ref.model, api_key=api_key or "not-needed", base_url=base_url)
    return CostMeter(
        client,
        db,
        phase,
        pricing=pricing,
        provider=ref.provider,
        local=provider_cfg.api_key_env is None and provider_cfg.api_key_keyring is None,
    )
