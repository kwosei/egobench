from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from rich.console import Console

from egobench.config import EgoBenchConfig
from egobench.db import DB, fetch_conversations
from egobench.ingest.base import first_user_text
from egobench.ingest.base import Turn as IngestTurn
from egobench.llm.base import Completion
from egobench.llm.factory import make_client
from egobench.llm.pricing import estimate_cost


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
MAX_WORKERS = 16
_PROMPT = (
    "Is the following message a genuine task or request for an AI assistant to act on? "
    "Reply only YES or NO.\n\nMessage: {text}"
)


def run(db: DB, cfg: EgoBenchConfig, console: Console | None = None) -> dict:
    console = console or Console()
    conversations = fetch_conversations(db)
    console.print(f"[dim]phase2: scanning {len(conversations)} conversations[/dim]")

    heuristic_rows: list[tuple] = []
    to_filter: list[tuple[int, str]] = []
    for conv in conversations:
        turns = [IngestTurn(**turn) for turn in conv["turns"]]
        first = first_user_text(turns)
        if _is_obvious_nontask(first):
            heuristic_rows.append((conv["id"], 0, first))
        else:
            to_filter.append((conv["id"], first))

    llm_rows: list[tuple] = []
    completions: list[Completion] = []
    if to_filter:
        filter_ref = cfg.filter.model_ref
        meter = make_client(filter_ref, cfg, None, "phase2")
        raw_client = meter.client
        console.print(
            f"[dim]phase2: classifying {len(to_filter)} candidates "
            f"with {filter_ref.display()} ({MAX_WORKERS} workers)[/dim]"
        )
        llm_rows, completions = _filter_concurrent(raw_client, to_filter, console)

    rows = heuristic_rows + llm_rows
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
        billable = [c for c in completions if c.billable]
        if billable:
            model_name = billable[0].model
            total_in = sum(c.usage.input_tokens for c in billable)
            total_out = sum(c.usage.output_tokens for c in billable)
            cost = estimate_cost(model_name, total_in, total_out)
            conn.execute(
                """
                INSERT INTO phase_cost_log(phase, model, input_tokens, output_tokens, cost_usd)
                VALUES (?, ?, ?, ?, ?)
                """,
                ("phase2", model_name, total_in, total_out, cost),
            )

    kept = sum(1 for _, is_task, _ in rows if is_task)
    dropped = sum(1 for _, is_task, _ in rows if not is_task)
    console.print(f"[dim]phase2: kept {kept} tasks, dropped {dropped} non-tasks[/dim]")
    return {"phase": 2, "kept": kept, "dropped": dropped}


def _filter_concurrent(
    client: Any,
    candidates: list[tuple[int, str]],
    console: Console,
) -> tuple[list[tuple], list[Completion]]:
    rows: list[tuple] = []
    completions: list[Completion] = []
    total = len(candidates)
    done = 0

    def _call(conv_id: int, text: str) -> tuple[int, bool, str, Completion | None]:
        prompt = _PROMPT.format(text=text[:2000])
        try:
            completion = client.complete(prompt)
            return conv_id, _parse_yes_no(completion.text), text, completion
        except Exception:
            return conv_id, True, text, None

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [executor.submit(_call, conv_id, text) for conv_id, text in candidates]
        for future in as_completed(futures):
            conv_id, is_task, text, completion = future.result()
            rows.append((conv_id, int(is_task), text))
            if completion is not None:
                completions.append(completion)
            done += 1
            if total >= 50 and done % max(1, total // 5) == 0:
                console.print(f"[dim]phase2: {done}/{total} classified[/dim]")

    return rows, completions


def _is_obvious_nontask(text: str) -> bool:
    if not text:
        return True
    normalized = re.sub(r"[^\w\s']", "", " ".join(text.lower().split())).strip()
    return normalized in ACKS or bool(PING_RE.match(normalized))


def _parse_yes_no(text: str) -> bool:
    upper = text.strip().upper()
    if upper.startswith("NO"):
        return False
    return True
