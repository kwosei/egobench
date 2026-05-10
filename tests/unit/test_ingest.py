from pathlib import Path

from egobench.ingest.chatgpt import ChatGPTAdapter
from egobench.ingest.claude import ClaudeAdapter
from egobench.ingest.jsonl import JSONLAdapter


FIXTURES = Path(__file__).parents[1] / "fixtures"


def test_chatgpt_adapter_loads_mapping_export():
    conversations = ChatGPTAdapter().load(FIXTURES / "chatgpt_export_sample.json")
    assert len(conversations) == 10
    assert conversations[0].id == "conv-debug-1"
    assert conversations[0].turns[0].role == "user"


def test_claude_adapter_loads_messages():
    conversations = ClaudeAdapter().load(FIXTURES / "claude_export_sample.json")
    assert len(conversations) == 1
    assert conversations[0].turns[0].text.startswith("Summarize")


def test_jsonl_adapter_loads_turns():
    conversations = JSONLAdapter().load(FIXTURES / "generic_sample.jsonl")
    assert len(conversations) == 1
    assert conversations[0].id == "jsonl-1"

