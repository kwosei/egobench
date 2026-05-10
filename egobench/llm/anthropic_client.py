from __future__ import annotations

from anthropic import Anthropic

from egobench.llm.base import Completion, Usage, estimate_tokens


class AnthropicClient:
    def __init__(self, model: str, api_key: str):
        self.model = model
        self._client = Anthropic(api_key=api_key)

    def complete(self, prompt: str, *, temperature: float = 0.0) -> Completion:
        response = self._client.messages.create(
            model=self.model,
            max_tokens=1200,
            temperature=temperature,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "\n".join(getattr(block, "text", "") for block in response.content).strip()
        usage = getattr(response, "usage", None)
        input_tokens = getattr(usage, "input_tokens", estimate_tokens(prompt)) if usage else estimate_tokens(prompt)
        output_tokens = getattr(usage, "output_tokens", estimate_tokens(text)) if usage else estimate_tokens(text)
        return Completion(text=text, model=self.model, usage=Usage(input_tokens, output_tokens))

