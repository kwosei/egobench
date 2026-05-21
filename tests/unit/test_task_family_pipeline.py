import json
import sqlite3
from io import StringIO
from types import SimpleNamespace

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
from egobench.db import init_db
from egobench.pipeline import (
    phase3_embed_cluster,
    phase4_categorize,
    phase5_importance,
    phase6_sample,
)


def test_db_migration_adds_task_family_columns(tmp_path):
    path = tmp_path / "egobench.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE task_candidates (
          conversation_id TEXT PRIMARY KEY,
          is_task INTEGER NOT NULL,
          first_user_text TEXT NOT NULL,
          cluster_id INTEGER,
          cluster_size INTEGER,
          category_label TEXT,
          category_description TEXT,
          importance REAL,
          selected INTEGER NOT NULL DEFAULT 0,
          checklist_json TEXT,
          raw_checklists_json TEXT,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    conn.close()

    init_db(path)

    conn = sqlite3.connect(path)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(task_candidates)")}
    conn.close()
    assert {
        "near_duplicate_group_id",
        "candidate_group_id",
        "task_family_id",
        "task_family",
        "domain",
        "skills_json",
        "family_importance",
    } <= columns


def test_phase3_assigns_candidate_and_near_duplicate_groups(tmp_path, monkeypatch):
    monkeypatch.delenv("TEST_API_KEY", raising=False)
    db = init_db(tmp_path / "egobench.db")
    _insert_candidates(
        db,
        [
            ("conv-1", "Translate hello to French"),
            ("conv-2", "Translate hello to French"),
            ("conv-3", "Write a SQL query for active users"),
        ],
    )

    result = phase3_embed_cluster.run(db, _cfg(), _quiet_console())

    assert result["tasks"] == 3
    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT conversation_id, candidate_group_id, candidate_group_size,
                   near_duplicate_group_id, near_duplicate_group_size
            FROM task_candidates
            ORDER BY conversation_id
            """
        ).fetchall()
    assert all(row["candidate_group_id"] is not None for row in rows)
    assert rows[0]["near_duplicate_group_id"] == rows[1]["near_duplicate_group_id"]
    assert rows[0]["near_duplicate_group_size"] == 2
    assert rows[2]["near_duplicate_group_size"] == 1


def test_phase4_parses_free_form_family_annotation(tmp_path, monkeypatch):
    db = init_db(tmp_path / "egobench.db")
    _insert_candidates(db, [("conv-1", "Explain the difference between depuis and pendant in French.")])
    with db.connect() as conn:
        conn.execute(
            """
            UPDATE task_candidates
            SET candidate_group_id = 0, candidate_group_size = 1,
                near_duplicate_group_id = 0, near_duplicate_group_size = 1
            """
        )

    class FakeClient:
        model = "fake"

        def complete(self, prompt: str, *, temperature: float = 0.0):
            return SimpleNamespace(
                text=json.dumps(
                    {
                        "group": {
                            "task_family": "French grammar distinction explanation",
                            "domain": "French language learning",
                            "skills": ["grammar explanation", "teaching"],
                            "group_summary": "French grammar questions.",
                        },
                        "annotations": [
                            {
                                "conversation_id": "conv-1",
                                "task_family": "French grammar distinction explanation",
                                "domain": "French language learning",
                                "skills": ["grammar explanation", "translation nuance"],
                                "family_fit": "strong",
                                "difficulty": "medium",
                                "specificity": "generalizable",
                            }
                        ],
                    }
                )
            )

    monkeypatch.setattr(phase4_categorize, "make_client", lambda *args, **kwargs: FakeClient())

    result = phase4_categorize.run(db, _cfg(), _quiet_console())

    assert result["families"] == 1
    with db.connect() as conn:
        row = conn.execute(
            "SELECT task_family_id, task_family, domain, skills_json FROM task_candidates"
        ).fetchone()
    assert row["task_family_id"]
    assert row["task_family"] == "French grammar distinction explanation"
    assert row["domain"] == "French language learning"
    assert json.loads(row["skills_json"]) == ["grammar explanation", "translation nuance"]


def test_phase4_canonicalizes_related_free_form_family_labels(tmp_path, monkeypatch):
    db = init_db(tmp_path / "egobench.db")
    _insert_candidates(
        db,
        [
            ("conv-1", "Explain the difference between depuis and pendant in French."),
            ("conv-2", "Explain when to use the French subjunctive."),
        ],
    )
    with db.connect() as conn:
        conn.execute(
            """
            UPDATE task_candidates
            SET candidate_group_id = 0, candidate_group_size = 1,
                near_duplicate_group_id = 0, near_duplicate_group_size = 1
            WHERE conversation_id = 'conv-1'
            """
        )
        conn.execute(
            """
            UPDATE task_candidates
            SET candidate_group_id = 1, candidate_group_size = 1,
                near_duplicate_group_id = 1, near_duplicate_group_size = 1
            WHERE conversation_id = 'conv-2'
            """
        )

    class FakeClient:
        model = "fake"

        def complete(self, prompt: str, *, temperature: float = 0.0):
            if "CANONICAL_FAMILY_MAP_JSON" in prompt:
                return SimpleNamespace(
                    text=json.dumps(
                        {
                            "families": [
                                {
                                    "source_labels": [
                                        "French grammar distinction explanation",
                                        "French grammar nuance explanation",
                                    ],
                                    "task_family": "French grammar explanation",
                                    "domain": "French language learning",
                                    "skills": ["grammar explanation", "teaching"],
                                }
                            ]
                        }
                    )
                )
            label = (
                "French grammar distinction explanation"
                if "depuis" in prompt
                else "French grammar nuance explanation"
            )
            return SimpleNamespace(
                text=json.dumps(
                    {
                        "group": {
                            "task_family": label,
                            "domain": "French language learning",
                            "skills": ["grammar explanation"],
                            "group_summary": "French grammar questions.",
                        },
                        "annotations": [
                            {
                                "conversation_id": "conv-1" if "depuis" in prompt else "conv-2",
                                "task_family": label,
                                "domain": "French language learning",
                                "skills": ["grammar explanation"],
                                "family_fit": "strong",
                                "difficulty": "medium",
                                "specificity": "generalizable",
                            }
                        ],
                    }
                )
            )

    monkeypatch.setattr(phase4_categorize, "make_client", lambda *args, **kwargs: FakeClient())

    result = phase4_categorize.run(db, _cfg(), _quiet_console())

    assert result["raw_families"] == 2
    assert result["families"] == 1
    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT task_family_id, task_family
            FROM task_candidates
            ORDER BY conversation_id
            """
        ).fetchall()
    assert rows[0]["task_family_id"] == rows[1]["task_family_id"]
    assert rows[0]["task_family"] == "French grammar explanation"
    assert rows[1]["task_family"] == "French grammar explanation"


def test_phase4_missing_batch_entry_falls_back_only_for_that_task(tmp_path, monkeypatch):
    db = init_db(tmp_path / "egobench.db")
    _insert_candidates(
        db,
        [
            ("conv-1", "Explain the difference between depuis and pendant in French."),
            ("conv-2", "Explain when to use the French subjunctive."),
        ],
    )
    with db.connect() as conn:
        conn.execute(
            """
            UPDATE task_candidates
            SET candidate_group_id = 0, candidate_group_size = 2,
                near_duplicate_group_id = 0, near_duplicate_group_size = 1
            """
        )

    class PartialClient:
        model = "partial"

        def complete(self, prompt: str, *, temperature: float = 0.0):
            return SimpleNamespace(
                text=json.dumps(
                    {
                        "group": {
                            "task_family": "French grammar explanation",
                            "domain": "French language learning",
                            "skills": ["grammar explanation"],
                            "group_summary": "French grammar questions.",
                        },
                        "annotations": [
                            {
                                "conversation_id": "conv-1",
                                "task_family": "French grammar explanation",
                                "domain": "French language learning",
                                "skills": ["grammar explanation"],
                                "family_fit": "strong",
                                "difficulty": "medium",
                                "specificity": "generalizable",
                            }
                        ],
                    }
                )
            )

    monkeypatch.setattr(phase4_categorize, "make_client", lambda *args, **kwargs: PartialClient())

    result = phase4_categorize.run(db, _cfg(), _quiet_console())

    assert result["annotation_batches"] == 1
    assert result["fallbacks"] == 1
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT conversation_id, task_family FROM task_candidates ORDER BY conversation_id"
        ).fetchall()
    assert rows[0]["task_family"] == rows[1]["task_family"] == "French grammar explanation"


def test_phase4_invalid_json_falls_back_gracefully(tmp_path, monkeypatch):
    db = init_db(tmp_path / "egobench.db")
    _insert_candidates(db, [("conv-1", "Create a launch checklist for a developer tool.")])
    with db.connect() as conn:
        conn.execute(
            """
            UPDATE task_candidates
            SET candidate_group_id = 0, candidate_group_size = 1,
                near_duplicate_group_id = 0, near_duplicate_group_size = 1
            """
        )

    class InvalidClient:
        model = "invalid"

        def complete(self, prompt: str, *, temperature: float = 0.0):
            return SimpleNamespace(text="not json")

    monkeypatch.setattr(phase4_categorize, "make_client", lambda *args, **kwargs: InvalidClient())

    result = phase4_categorize.run(db, _cfg(), _quiet_console())

    assert result["fallbacks"] == 1
    with db.connect() as conn:
        row = conn.execute("SELECT task_family, difficulty, specificity FROM task_candidates").fetchone()
    assert row["task_family"]
    assert row["difficulty"] in {"easy", "medium", "hard"}
    assert row["specificity"] in {"generalizable", "narrow", "one_off"}


def test_phase5_computes_family_sizes_and_importance(tmp_path):
    db = init_db(tmp_path / "egobench.db")
    _insert_candidates(
        db,
        [
            ("conv-1", "Task A one"),
            ("conv-2", "Task A two"),
            ("conv-3", "Task B one"),
        ],
    )
    with db.connect() as conn:
        conn.execute(
            """
            UPDATE task_candidates
            SET task_family_id = 'family-a', task_family = 'Family A',
                difficulty = 'medium', specificity = 'generalizable',
                near_duplicate_group_id = 1
            WHERE conversation_id = 'conv-1'
            """
        )
        conn.execute(
            """
            UPDATE task_candidates
            SET task_family_id = 'family-a', task_family = 'Family A variant',
                difficulty = 'hard', specificity = 'narrow',
                near_duplicate_group_id = 2
            WHERE conversation_id = 'conv-2'
            """
        )
        conn.execute(
            """
            UPDATE task_candidates
            SET task_family = 'Family B',
                difficulty = 'easy', specificity = 'one_off',
                near_duplicate_group_id = 3
            WHERE conversation_id = 'conv-3'
            """
        )

    result = phase5_importance.run(db, _quiet_console())

    assert result["families"] == 2
    with db.connect() as conn:
        rows = {
            row["conversation_id"]: dict(row)
            for row in conn.execute(
                "SELECT conversation_id, family_size, family_importance FROM task_candidates"
            )
        }
    assert rows["conv-1"]["family_size"] == 2
    assert rows["conv-2"]["family_size"] == 2
    assert rows["conv-1"]["family_importance"] > rows["conv-3"]["family_importance"]


def test_phase6_family_sampler_balances_recurring_long_tail_and_duplicates():
    cfg = _cfg(sample=SampleCfg(target_n=5, max_family_tasks=2, long_tail_fraction=0.20))
    rows = [
        _sample_row("a-1", "Family A", 4, 1.0, near_duplicate_group_id=10, near_duplicate_group_size=2),
        _sample_row("a-2", "Family A", 4, 1.0, near_duplicate_group_id=10, near_duplicate_group_size=2),
        _sample_row("a-3", "Family A", 4, 1.0, near_duplicate_group_id=11),
        _sample_row("a-4", "Family A", 4, 1.0, near_duplicate_group_id=12),
        _sample_row("b-1", "Family B", 3, 0.8, near_duplicate_group_id=20),
        _sample_row("b-2", "Family B", 3, 0.8, near_duplicate_group_id=21),
        _sample_row("c-1", "Family C", 1, 0.3, near_duplicate_group_id=30),
        _sample_row("d-1", "Family D", 1, 0.2, near_duplicate_group_id=40),
        _sample_row("e-1", "Family E", 1, 0.1, near_duplicate_group_id=50),
    ]

    selected, stats = phase6_sample._select(rows, 5, cfg)

    selected_ids = {row["conversation_id"] for row in selected}
    selected_families = [row["task_family"] for row in selected]
    selected_duplicate_groups = [row["near_duplicate_group_id"] for row in selected]
    assert len(selected) == 5
    assert not {"a-1", "a-2"} <= selected_ids
    assert len(selected_duplicate_groups) == len(set(selected_duplicate_groups))
    assert selected_families.count("Family A") <= 2
    assert any(row["family_size"] > 1 for row in selected)
    assert any(row["family_size"] <= 2 for row in selected)
    assert stats["duplicate_groups_suppressed"] == 1


def _insert_candidates(db, rows: list[tuple[str, str]]) -> None:
    with db.connect() as conn:
        conn.executemany(
            """
            INSERT INTO task_candidates(conversation_id, is_task, first_user_text)
            VALUES (?, 1, ?)
            """,
            rows,
        )


def _sample_row(
    conversation_id: str,
    family: str,
    family_size: int,
    importance: float,
    *,
    near_duplicate_group_id: int,
    near_duplicate_group_size: int = 1,
) -> dict:
    return {
        "conversation_id": conversation_id,
        "task_family_id": family.lower().replace(" ", "-"),
        "task_family": family,
        "specificity": "generalizable",
        "difficulty": "medium",
        "family_size": family_size,
        "family_importance": importance,
        "near_duplicate_group_id": near_duplicate_group_id,
        "near_duplicate_group_size": near_duplicate_group_size,
    }


def _cfg(sample: SampleCfg | None = None) -> EgoBenchConfig:
    return EgoBenchConfig(
        workspace=WorkspaceCfg(seed=42),
        providers={"test": ProviderCfg(name="test", api_key_env="TEST_API_KEY")},
        filter=FilterCfg(ModelRef(provider="test", model="filter")),
        judges=JudgesCfg(
            default=ModelRef(provider="test", model="judge"),
            checklist_panel=[ModelRef(provider="test", model="panel")],
        ),
        embeddings=EmbeddingsCfg(provider="test", model="embedding"),
        sample=sample or SampleCfg(),
    )


def _quiet_console() -> Console:
    return Console(file=StringIO(), force_terminal=False, color_system=None)
