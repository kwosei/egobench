from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from egobench.ingest.base import Conversation


SCHEMA_VERSION = 3


TASK_CANDIDATE_COLUMN_DEFS = {
    "near_duplicate_group_id": "INTEGER",
    "near_duplicate_group_size": "INTEGER",
    "candidate_group_id": "INTEGER",
    "candidate_group_size": "INTEGER",
    "task_family_id": "TEXT",
    "task_family": "TEXT",
    "domain": "TEXT",
    "skills_json": "TEXT",
    "family_fit": "TEXT",
    "difficulty": "TEXT",
    "specificity": "TEXT",
    "family_size": "INTEGER",
    "family_importance": "REAL",
}


@dataclass(frozen=True)
class DB:
    path: Path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()


def init_db(path: Path) -> DB:
    db = DB(path)
    with db.connect() as conn:
        conn.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS meta (
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS conversations (
              id TEXT PRIMARY KEY,
              model_used TEXT,
              metadata_json TEXT NOT NULL DEFAULT '{}',
              source_adapter TEXT NOT NULL,
              imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS turns (
              conversation_id TEXT NOT NULL,
              turn_index INTEGER NOT NULL,
              role TEXT NOT NULL,
              text TEXT NOT NULL,
              ts TEXT,
              PRIMARY KEY (conversation_id, turn_index),
              FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS task_candidates (
              conversation_id TEXT PRIMARY KEY,
              is_task INTEGER NOT NULL,
              first_user_text TEXT NOT NULL,
              cluster_id INTEGER,
              cluster_size INTEGER,
              near_duplicate_group_id INTEGER,
              near_duplicate_group_size INTEGER,
              candidate_group_id INTEGER,
              candidate_group_size INTEGER,
              category_label TEXT,
              category_description TEXT,
              task_family_id TEXT,
              task_family TEXT,
              domain TEXT,
              skills_json TEXT,
              family_fit TEXT,
              difficulty TEXT,
              specificity TEXT,
              family_size INTEGER,
              family_importance REAL,
              importance REAL,
              selected INTEGER NOT NULL DEFAULT 0,
              checklist_json TEXT,
              raw_checklists_json TEXT,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS benchmark_versions (
              version INTEGER PRIMARY KEY AUTOINCREMENT,
              benchmark_hash TEXT NOT NULL,
              path TEXT NOT NULL,
              task_count INTEGER NOT NULL,
              config_json TEXT NOT NULL,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS phase_cache (
              phase TEXT PRIMARY KEY,
              cache_key TEXT NOT NULL,
              output_json TEXT NOT NULL,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS phase_cost_log (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              phase TEXT NOT NULL,
              model TEXT NOT NULL,
              input_tokens INTEGER NOT NULL,
              output_tokens INTEGER NOT NULL,
              cost_usd REAL NOT NULL,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        _migrate_task_candidates(conn)
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
    return db


def _migrate_task_candidates(conn: sqlite3.Connection) -> None:
    existing = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(task_candidates)").fetchall()
    }
    for column, ddl in TASK_CANDIDATE_COLUMN_DEFS.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE task_candidates ADD COLUMN {column} {ddl}")


def insert_conversations(db: DB, conversations: list[Conversation], source_adapter: str) -> int:
    with db.connect() as conn:
        for conv in conversations:
            conn.execute(
                """
                INSERT OR REPLACE INTO conversations(id, model_used, metadata_json, source_adapter)
                VALUES (?, ?, ?, ?)
                """,
                (conv.id, conv.model_used, json.dumps(conv.metadata, sort_keys=True), source_adapter),
            )
            conn.execute("DELETE FROM turns WHERE conversation_id = ?", (conv.id,))
            rows = [
                (conv.id, idx, turn.role, turn.text, turn.ts)
                for idx, turn in enumerate(conv.turns)
            ]
            conn.executemany(
                """
                INSERT INTO turns(conversation_id, turn_index, role, text, ts)
                VALUES (?, ?, ?, ?, ?)
                """,
                rows,
            )
    return len(conversations)


def fetch_conversations(db: DB) -> list[dict]:
    with db.connect() as conn:
        conv_rows = conn.execute(
            "SELECT id, model_used, metadata_json, source_adapter FROM conversations ORDER BY id"
        ).fetchall()
        out: list[dict] = []
        for row in conv_rows:
            turns = conn.execute(
                """
                SELECT role, text, ts FROM turns
                WHERE conversation_id = ?
                ORDER BY turn_index
                """,
                (row["id"],),
            ).fetchall()
            out.append(
                {
                    "id": row["id"],
                    "model_used": row["model_used"],
                    "metadata": json.loads(row["metadata_json"] or "{}"),
                    "source_adapter": row["source_adapter"],
                    "turns": [dict(turn) for turn in turns],
                }
            )
        return out


def latest_benchmark_hash(db: DB) -> str | None:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT benchmark_hash FROM benchmark_versions ORDER BY version DESC LIMIT 1"
        ).fetchone()
        return None if row is None else str(row["benchmark_hash"])
