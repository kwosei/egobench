from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelPrice:
    input_per_1k: float
    output_per_1k: float


PRICES: dict[str, ModelPrice] = {
    "claude-opus-4-7": ModelPrice(input_per_1k=0.015, output_per_1k=0.075),
    "claude-sonnet-4": ModelPrice(input_per_1k=0.003, output_per_1k=0.015),
    "gpt-5": ModelPrice(input_per_1k=0.005, output_per_1k=0.015),
    "gpt-4.1": ModelPrice(input_per_1k=0.002, output_per_1k=0.008),
    "text-embedding-3-small": ModelPrice(input_per_1k=0.00002, output_per_1k=0.0),
}


def price_for(model: str) -> ModelPrice:
    if model in PRICES:
        return PRICES[model]
    # OpenRouter-style "vendor/model" — strip the vendor prefix and retry.
    if "/" in model:
        _, _, bare = model.partition("/")
        if bare in PRICES:
            return PRICES[bare]
    return ModelPrice(input_per_1k=0.0, output_per_1k=0.0)


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    price = price_for(model)
    return (input_tokens / 1000.0 * price.input_per_1k) + (output_tokens / 1000.0 * price.output_per_1k)

