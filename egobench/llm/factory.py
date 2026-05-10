from __future__ import annotations

from egobench.config import EgoBenchConfig
from egobench.cost.tracker import CostMeter
from egobench.db import DB
from egobench.llm.anthropic_client import AnthropicClient
from egobench.llm.openai_client import OpenAIClient
from egobench.llm.recorded import RecordedLLMClient


def make_client(model: str, cfg: EgoBenchConfig, db: DB | None, phase: str) -> CostMeter:
    key = cfg.api_key_for_model(model)
    provider = cfg.model_provider(model)
    if key and provider == "anthropic":
        client = AnthropicClient(model=model, api_key=key)
    elif key and provider == "openai":
        client = OpenAIClient(model=model, api_key=key)
    else:
        client = RecordedLLMClient(model=model)
    return CostMeter(client, db, phase)

