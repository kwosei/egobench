from __future__ import annotations

from openai import OpenAI

from egobench.llm.base import Completion, Usage, estimate_tokens


MAX_COMPLETION_TOKENS = 4096


class OpenAIClient:
    def __init__(
        self,
        model: str,
        api_key: str,
        base_url: str | None = None,
        timeout_seconds: float | None = None,
    ):
        self.model = model
        kwargs: dict = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        if timeout_seconds is not None:
            kwargs["timeout"] = timeout_seconds
        self._client = OpenAI(**kwargs)

    def complete(self, prompt: str, *, temperature: float = 0.0) -> Completion:
        # Many newer models (Opus 4.7, GPT-5) reject or ignore `temperature`, so
        # we let each provider pick its own default rather than pass it through.
        _ = temperature
        response = self._create_completion(prompt)
        choice = response.choices[0]
        text = (choice.message.content or "").strip()
        usage = getattr(response, "usage", None)
        input_tokens = getattr(usage, "prompt_tokens", estimate_tokens(prompt)) if usage else estimate_tokens(prompt)
        output_tokens = getattr(usage, "completion_tokens", estimate_tokens(text)) if usage else estimate_tokens(text)
        return Completion(text=text, model=self.model, usage=Usage(input_tokens, output_tokens))

    def _create_completion(self, prompt: str):
        kwargs = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_completion_tokens": MAX_COMPLETION_TOKENS,
        }
        try:
            return self._client.chat.completions.create(**kwargs)
        except Exception as err:
            if not _is_unsupported_param(err, "max_completion_tokens"):
                raise
            kwargs.pop("max_completion_tokens")
            kwargs["max_tokens"] = MAX_COMPLETION_TOKENS
            return self._client.chat.completions.create(**kwargs)


def _is_unsupported_param(err: Exception, param: str) -> bool:
    body = getattr(err, "body", None)
    if isinstance(body, dict):
        error = body.get("error", {})
        if isinstance(error, dict) and error.get("param") == param:
            return True
    message = str(err)
    return param in message and "unsupported" in message.lower()
