import json
from io import StringIO

import pytest
from rich.console import Console

from egobench.config import (
    EgoBenchConfig,
    EmbeddingsCfg,
    FilterCfg,
    JudgesCfg,
    ModelRef,
    ProviderCfg,
    SampleCfg,
    WorkspaceCfg,
)
from egobench.db import init_db, insert_conversations
from egobench.ingest.base import Conversation, Turn
from egobench.pipeline import phase7_checklist


class FailingClient:
    model = "failing-model"

    def complete(self, prompt: str, *, temperature: float = 0.0):
        _ = prompt
        _ = temperature
        raise RuntimeError("model unavailable")


class BatchClient:
    model = "batch-model"

    def __init__(self, model: str):
        self.model = model

    def complete(self, prompt: str, *, temperature: float = 0.0):
        _ = temperature
        if "MERGE_CHECKLIST_BATCH_JSON" in prompt:
            return _json_response(
                {
                    "checklists": [
                        {"conversation_id": "conv-1", "items": ["merged one", "shared"]},
                        {"conversation_id": "conv-2", "items": ["merged two", "shared"]},
                    ]
                }
            )
        return _json_response(
            {
                "checklists": [
                    {"conversation_id": "conv-1", "items": [f"{self.model} one", "shared"]},
                    {"conversation_id": "conv-2", "items": [f"{self.model} two", "shared"]},
                ]
            }
        )


def test_phase7_falls_back_when_model_calls_fail(tmp_path, monkeypatch):
    db = init_db(tmp_path / "egobench.db")
    insert_conversations(
        db,
        [
            Conversation(
                id="conv-1",
                turns=[Turn(role="user", text="Create a launch checklist for a developer tool.")],
            )
        ],
        "jsonl",
    )
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO task_candidates(conversation_id, is_task, first_user_text, selected)
            VALUES (?, ?, ?, ?)
            """,
            ("conv-1", 1, "Create a launch checklist for a developer tool.", 1),
        )

    cfg = EgoBenchConfig(
        workspace=WorkspaceCfg(),
        providers={"test": ProviderCfg(name="test")},
        filter=FilterCfg(ModelRef(provider="test", model="filter")),
        judges=JudgesCfg(
            default=ModelRef(provider="test", model="merge"),
            checklist_panel=[ModelRef(provider="test", model="panel")],
        ),
        embeddings=EmbeddingsCfg(provider="test", model="embedding"),
        sample=SampleCfg(),
    )
    monkeypatch.setattr(phase7_checklist, "make_client", lambda *args, **kwargs: FailingClient())

    output = StringIO()
    console = Console(file=output, force_terminal=False, color_system=None)
    result = phase7_checklist.run(db, cfg, console)

    assert result == {"phase": 7, "checklists": 1, "panel_batches": 1, "merge_batches": 1}
    assert "panel test:panel failed" in output.getvalue()
    assert "merge test:merge failed" in output.getvalue()
    with db.connect() as conn:
        row = conn.execute(
            "SELECT checklist_json, raw_checklists_json FROM task_candidates WHERE conversation_id = ?",
            ("conv-1",),
        ).fetchone()
    checklist = json.loads(row["checklist_json"])
    raw = json.loads(row["raw_checklists_json"])
    assert checklist
    assert raw["test:panel"]


def test_phase7_batches_panel_and_merge_outputs_by_task(tmp_path, monkeypatch):
    db = init_db(tmp_path / "egobench.db")
    insert_conversations(
        db,
        [
            Conversation(id="conv-1", turns=[Turn(role="user", text="Draft a launch email.")]),
            Conversation(id="conv-2", turns=[Turn(role="user", text="Explain a Python API.")]),
        ],
        "jsonl",
    )
    with db.connect() as conn:
        conn.executemany(
            """
            INSERT INTO task_candidates(
              conversation_id, is_task, first_user_text, selected,
              task_family, domain, skills_json, difficulty, specificity
            )
            VALUES (?, 1, ?, 1, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "conv-1",
                    "Draft a launch email.",
                    "Launch writing",
                    "Writing",
                    json.dumps(["copywriting"]),
                    "medium",
                    "generalizable",
                ),
                (
                    "conv-2",
                    "Explain a Python API.",
                    "API explanation",
                    "Software engineering",
                    json.dumps(["technical explanation"]),
                    "medium",
                    "generalizable",
                ),
            ],
        )

    cfg = EgoBenchConfig(
        workspace=WorkspaceCfg(),
        providers={"test": ProviderCfg(name="test")},
        filter=FilterCfg(ModelRef(provider="test", model="filter")),
        judges=JudgesCfg(
            default=ModelRef(provider="test", model="merge"),
            checklist_panel=[ModelRef(provider="test", model="panel")],
        ),
        embeddings=EmbeddingsCfg(provider="test", model="embedding"),
        sample=SampleCfg(),
    )
    monkeypatch.setattr(
        phase7_checklist,
        "make_client",
        lambda ref, *args, **kwargs: BatchClient(ref.model),
    )

    result = phase7_checklist.run(db, cfg, Console(file=StringIO(), force_terminal=False, color_system=None))

    assert result == {"phase": 7, "checklists": 2, "panel_batches": 1, "merge_batches": 1}
    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT conversation_id, checklist_json, raw_checklists_json
            FROM task_candidates
            ORDER BY conversation_id
            """
        ).fetchall()
    first = json.loads(rows[0]["checklist_json"])
    second = json.loads(rows[1]["checklist_json"])
    first_raw = json.loads(rows[0]["raw_checklists_json"])
    second_raw = json.loads(rows[1]["raw_checklists_json"])
    assert first == ["merged one", "shared"]
    assert second == ["merged two", "shared"]
    assert first_raw["test:panel"] == ["panel one", "shared"]
    assert second_raw["test:panel"] == ["panel two", "shared"]


def test_json_object_repairs_common_batch_json_errors():
    payload = phase7_checklist._json_object(
        """
        ```json
        {
          "checklists": [
            {"conversation_id": "conv-1", "items": ["one", "shared",]}
            {"conversation_id": "conv-2", "items": ["two" "shared"]}
          ]
        }
        ```
        """
    )

    assert payload == {
        "checklists": [
            {"conversation_id": "conv-1", "items": ["one", "shared"]},
            {"conversation_id": "conv-2", "items": ["two", "shared"]},
        ]
    }


def test_json_object_reports_empty_model_response():
    with pytest.raises(ValueError, match="empty model response"):
        phase7_checklist._json_object("")


def _json_response(payload: dict):
    return type("Response", (), {"text": json.dumps(payload)})()
