from __future__ import annotations

from egobench.db import DB
from egobench.llm.base import Completion, LLMClient
from egobench.llm.pricing import PricingResolver, estimate_cost


class CostMeter:
    def __init__(
        self,
        client: LLMClient,
        db: DB | None,
        phase: str,
        *,
        pricing: PricingResolver | None = None,
        provider: str | None = None,
        local: bool = False,
    ):
        self.client = client
        self.db = db
        self.phase = phase
        self.model = client.model
        self.pricing = pricing
        self.provider = provider
        self.local = local

    def complete(self, prompt: str, *, temperature: float = 0.0) -> Completion:
        completion = self.client.complete(prompt, temperature=temperature)
        cost = (
            estimate_cost(
                completion.model,
                completion.usage.input_tokens,
                completion.usage.output_tokens,
                provider=self.provider,
                resolver=self.pricing,
                local=self.local,
            )
            if completion.billable
            else 0.0
        )
        if self.db is not None:
            with self.db.connect() as conn:
                conn.execute(
                    """
                    INSERT INTO phase_cost_log(phase, model, input_tokens, output_tokens, cost_usd)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        self.phase,
                        completion.model,
                        completion.usage.input_tokens,
                        completion.usage.output_tokens,
                        cost,
                    ),
                )
        return completion
