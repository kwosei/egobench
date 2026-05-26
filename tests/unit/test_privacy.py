import json
from io import StringIO

from rich.console import Console

from egobench.config import PrivacyCfg
from egobench.db import fetch_conversations, init_db
from egobench.pipeline.phase1_ingest import run as run_ingest_phase
from egobench.privacy import RegexPrivacyRedactor, RedactionSpan, apply_redactions


def test_regex_privacy_redactor_masks_structured_pii():
    redactor = RegexPrivacyRedactor()

    result = redactor.redact(
        "Email alice@example.com or call (415) 555-1212. "
        "The API key is sk-abcdefghijklmnopqrstuvwxyz."
    )

    assert "alice@example.com" not in result.text
    assert "(415) 555-1212" not in result.text
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in result.text
    assert "[private_email]" in result.text
    assert "[private_phone]" in result.text
    assert "[secret]" in result.text
    assert {span.label for span in result.spans} >= {"private_email", "private_phone", "secret"}


def test_apply_redactions_handles_overlapping_spans():
    redacted = apply_redactions(
        "call 415-555-1212",
        [
            RedactionSpan(5, 17, "account_number"),
            RedactionSpan(5, 12, "private_phone"),
        ],
        replacement="<{label}>",
    )

    assert redacted == "call <account_number>"


def test_ingest_redacts_before_database_insert(tmp_path):
    export_path = tmp_path / "pii.jsonl"
    export_path.write_text(
        json.dumps(
            {
                "id": "conv-1",
                "metadata": {"title": "Email Alice at alice@example.com"},
                "turns": [
                    {
                        "role": "user",
                        "text": "Please email alice@example.com and call 415-555-1212.",
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    db = init_db(tmp_path / "egobench.db")

    result = run_ingest_phase(
        db,
        export_path,
        "jsonl",
        Console(file=StringIO(), force_terminal=False, color_system=None),
        PrivacyCfg(enabled=True, backend="regex"),
    )

    assert result["redactions"] == 3
    conversations = fetch_conversations(db)
    turn_text = conversations[0]["turns"][0]["text"]
    metadata = conversations[0]["metadata"]
    assert "alice@example.com" not in turn_text
    assert "415-555-1212" not in turn_text
    assert "alice@example.com" not in metadata["title"]
    assert metadata["privacy_redacted"] is True
    assert metadata["privacy_redaction_count"] == 3
