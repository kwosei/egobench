from types import SimpleNamespace

from egobench.llm.openai_client import MAX_COMPLETION_TOKENS, OpenAIClient


class UnsupportedParamError(Exception):
    def __init__(self, param: str):
        self.body = {"error": {"param": param, "message": f"Unsupported parameter: {param}"}}
        super().__init__(self.body["error"]["message"])


def _response(text: str = "ok"):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
        usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2),
    )


def test_openai_client_uses_max_completion_tokens(monkeypatch):
    calls: list[dict] = []

    class FakeCompletions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return _response()

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    monkeypatch.setattr("egobench.llm.openai_client.OpenAI", lambda **kwargs: fake_client)

    completion = OpenAIClient(model="gpt-5", api_key="test").complete("hello")

    assert completion.text == "ok"
    assert calls[0]["max_completion_tokens"] == MAX_COMPLETION_TOKENS
    assert "max_tokens" not in calls[0]


def test_openai_client_falls_back_to_legacy_max_tokens(monkeypatch):
    calls: list[dict] = []

    class FakeCompletions:
        def create(self, **kwargs):
            calls.append(kwargs)
            if "max_completion_tokens" in kwargs:
                raise UnsupportedParamError("max_completion_tokens")
            return _response()

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    monkeypatch.setattr("egobench.llm.openai_client.OpenAI", lambda **kwargs: fake_client)

    completion = OpenAIClient(model="legacy-compatible", api_key="test").complete("hello")

    assert completion.text == "ok"
    assert calls[0]["max_completion_tokens"] == MAX_COMPLETION_TOKENS
    assert calls[1]["max_tokens"] == MAX_COMPLETION_TOKENS
    assert "max_completion_tokens" not in calls[1]
