from __future__ import annotations

from openai import OpenAI

from egobench.llm.base import Completion, Usage, estimate_tokens


class OpenAIClient:
    def __init__(self, model: str, api_key: str, base_url: str | None = None):
        self.model = model
        kwargs: dict = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = OpenAI(**kwargs)

    def complete(self, prompt: str, *, temperature: float = 0.0) -> Completion:
        # Many newer models (Opus 4.7, GPT-5) reject or ignore `temperature`, so
        # we let each provider pick its own default rather than pass it through.
        _ = temperature
        response = self._client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1200,
        )
        choice = response.choices[0]
        text = (choice.message.content or "").strip()
        usage = getattr(response, "usage", None)
        input_tokens = getattr(usage, "prompt_tokens", estimate_tokens(prompt)) if usage else estimate_tokens(prompt)
        output_tokens = getattr(usage, "completion_tokens", estimate_tokens(text)) if usage else estimate_tokens(text)
        return Completion(text=text, model=self.model, usage=Usage(input_tokens, output_tokens))
