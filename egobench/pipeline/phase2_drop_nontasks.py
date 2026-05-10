from __future__ import annotations

import re

from egobench.db import DB, fetch_conversations
from egobench.ingest.base import first_user_text
from egobench.ingest.base import Turn as IngestTurn


ACKS = {
    "ok",
    "okay",
    "thanks",
    "thank you",
    "ty",
    "cool",
    "great",
    "got it",
    "sounds good",
    "hello",
    "hi",
    "hey",
    "yo",
}
PING_RE = re.compile(r"^(are you there|test|testing|ping|hello\??|hi\??|hey\??)$", re.I)


def run(db: DB) -> dict:
    conversations = fetch_conversations(db)
    rows: list[tuple] = []
    kept = 0
    dropped = 0
    for conv in conversations:
        turns = [IngestTurn(**turn) for turn in conv["turns"]]
        first = first_user_text(turns)
        is_task = _is_task(first)
        kept += int(is_task)
        dropped += int(not is_task)
        rows.append((conv["id"], int(is_task), first))
    with db.connect() as conn:
        conn.executemany(
            """
            INSERT INTO task_candidates(conversation_id, is_task, first_user_text)
            VALUES (?, ?, ?)
            ON CONFLICT(conversation_id) DO UPDATE SET
              is_task = excluded.is_task,
              first_user_text = excluded.first_user_text,
              updated_at = CURRENT_TIMESTAMP
            """,
            rows,
        )
    return {"phase": 2, "kept": kept, "dropped": dropped}


def _is_task(first_user: str) -> bool:
    text = " ".join(first_user.lower().split())
    if not text:
        return False
    normalized = re.sub(r"[^\w\s']", "", text).strip()
    if normalized in ACKS:
        return False
    if PING_RE.match(normalized):
        return False
    return True

