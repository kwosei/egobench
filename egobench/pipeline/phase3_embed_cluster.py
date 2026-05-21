from __future__ import annotations

import hashlib
import math
import re
from collections import Counter, defaultdict

import numpy as np
from rich.console import Console

from egobench.config import EgoBenchConfig
from egobench.db import DB
from egobench.llm.base import estimate_tokens
from egobench.llm.pricing import estimate_cost


DIM = 32
STOP = {
    "the",
    "and",
    "for",
    "with",
    "this",
    "that",
    "you",
    "can",
    "please",
    "help",
    "need",
    "want",
    "about",
    "from",
    "into",
    "what",
    "when",
    "where",
    "why",
    "how",
}


def run(db: DB, cfg: EgoBenchConfig, console: Console | None = None) -> dict:
    console = console or Console()
    tasks = _task_rows(db)
    console.print(f"[dim]phase3: embedding and clustering {len(tasks)} tasks[/dim]")
    if not tasks:
        return {"phase": 3, "clusters": 0}
    texts = [row["first_user_text"] for row in tasks]
    embeddings = _embed_texts(texts, cfg, db, console)
    candidate_labels = _cluster(embeddings, [row["first_user_text"] for row in tasks], cfg, console)
    candidate_sizes = Counter(candidate_labels)
    duplicate_labels = _near_duplicate_labels(embeddings, cfg.sample.near_duplicate_threshold)
    duplicate_sizes = Counter(duplicate_labels)
    with db.connect() as conn:
        conn.executemany(
            """
            UPDATE task_candidates
            SET cluster_id = ?,
                cluster_size = ?,
                candidate_group_id = ?,
                candidate_group_size = ?,
                near_duplicate_group_id = ?,
                near_duplicate_group_size = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE conversation_id = ?
            """,
            [
                (
                    int(candidate_labels[idx]),
                    int(candidate_sizes[candidate_labels[idx]]),
                    int(candidate_labels[idx]),
                    int(candidate_sizes[candidate_labels[idx]]),
                    int(duplicate_labels[idx]),
                    int(duplicate_sizes[duplicate_labels[idx]]),
                    tasks[idx]["conversation_id"],
                )
                for idx in range(len(tasks))
            ],
        )
    duplicate_group_count = len(duplicate_sizes)
    suppressed = len(tasks) - duplicate_group_count
    console.print(
        f"[dim]phase3: assigned {len(tasks)} tasks across {len(candidate_sizes)} candidate groups "
        f"and {duplicate_group_count} near-duplicate groups[/dim]"
    )
    return {
        "phase": 3,
        "candidate_groups": len(candidate_sizes),
        "near_duplicate_groups": duplicate_group_count,
        "duplicates_suppressed": suppressed,
        "tasks": len(tasks),
    }


def _task_rows(db: DB) -> list[dict]:
    with db.connect() as conn:
        return [
            dict(row)
            for row in conn.execute(
                """
                SELECT conversation_id, first_user_text
                FROM task_candidates
                WHERE is_task = 1
                ORDER BY conversation_id
                """
            )
        ]


def _embed_text(text: str) -> list[float]:
    vec = [0.0] * DIM
    for token in _tokens(text):
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        idx = digest[0] % DIM
        sign = 1.0 if digest[1] % 2 == 0 else -1.0
        vec[idx] += sign
    norm = math.sqrt(sum(value * value for value in vec)) or 1.0
    return [value / norm for value in vec]


def _embed_texts(texts: list[str], cfg: EgoBenchConfig, db: DB, console: Console) -> list[list[float]]:
    provider_cfg = cfg.provider(cfg.embeddings.provider)
    model = cfg.embeddings.model
    console.print(f"[dim]phase3: embedding with {provider_cfg.name}:{model}[/dim]")

    api_key = cfg.api_key_for_provider(provider_cfg.name)
    if provider_cfg.api_key_env and not api_key:
        console.print(
            f"[dim]phase3: {provider_cfg.api_key_env} is unset; using deterministic heuristic embeddings[/dim]"
        )
        return [_embed_text(text) for text in texts]
    try:
        from openai import OpenAI

        client_kwargs: dict = {"api_key": api_key or "not-needed"}
        if provider_cfg.base_url:
            client_kwargs["base_url"] = provider_cfg.base_url
        client = OpenAI(**client_kwargs)
        response = client.embeddings.create(model=model, input=texts)
        vectors = [item.embedding for item in response.data]
        usage = getattr(response, "usage", None)
        input_tokens = getattr(usage, "prompt_tokens", sum(estimate_tokens(text) for text in texts))
        # Local servers don't bill us — only charge when the model has a known
        # price entry.
        cost = 0.0 if provider_cfg.base_url else estimate_cost(model, int(input_tokens), 0)
        with db.connect() as conn:
            conn.execute(
                """
                INSERT INTO phase_cost_log(phase, model, input_tokens, output_tokens, cost_usd)
                VALUES (?, ?, ?, ?, ?)
                """,
                ("phase3", model, int(input_tokens), 0, cost),
            )
        return _unit_vectors(vectors)
    except Exception as err:
        console.print(f"[dim]phase3: embedding API failed ({err}); using deterministic heuristic embeddings[/dim]")
        return [_embed_text(text) for text in texts]


def _unit_vectors(vectors: list[list[float]]) -> list[list[float]]:
    normalized: list[list[float]] = []
    for vector in vectors:
        norm = np.linalg.norm(vector)
        if not norm:
            normalized.append([0.0 for _ in vector])
            continue
        normalized.append([float(value) / norm for value in vector])
    return normalized


def _near_duplicate_labels(embeddings: list[list[float]], threshold: float) -> list[int]:
    if not embeddings:
        return []
    vectors = embeddings
    parent = list(range(len(vectors)))

    def find(idx: int) -> int:
        while parent[idx] != idx:
            parent[idx] = parent[parent[idx]]
            idx = parent[idx]
        return idx

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root == right_root:
            return
        parent[max(left_root, right_root)] = min(left_root, right_root)

    for left in range(len(vectors)):
        for right in range(left + 1, len(vectors)):
            similarity = float(np.dot(vectors[left], vectors[right]))
            if similarity >= threshold:
                union(left, right)

    roots = [find(idx) for idx in range(len(vectors))]
    ordered_roots = sorted(set(roots), key=roots.index)
    mapping = {root: idx for idx, root in enumerate(ordered_roots)}
    return [mapping[root] for root in roots]


def _tokens(text: str) -> list[str]:
    return [
        token.lower()
        for token in re.findall(r"[a-zA-Z][a-zA-Z0-9_+-]{2,}", text)
        if token.lower() not in STOP
    ]


def _cluster(
    embeddings: list[list[float]],
    texts: list[str],
    cfg: EgoBenchConfig,
    console: Console,
) -> list[int]:
    if len(embeddings) < 3:
        console.print("[dim]phase3: fewer than 3 tasks; using heuristic clustering[/dim]")
        return _heuristic_labels(texts)
    try:
        import hdbscan  # type: ignore

        min_cluster_size = max(3, len(embeddings) // 50)
        console.print(f"[dim]phase3: clustering with hdbscan (min_cluster_size={min_cluster_size})[/dim]")
        raw = hdbscan.HDBSCAN(min_cluster_size=min_cluster_size, prediction_data=False).fit_predict(np.array(embeddings))
        if len(set(raw)) > 1:
            return _normalize_labels(raw.tolist(), texts)
        console.print("[dim]phase3: hdbscan produced one cluster; using heuristic clustering[/dim]")
    except Exception as err:
        console.print(f"[dim]phase3: hdbscan unavailable or failed ({err}); using heuristic clustering[/dim]")
    return _heuristic_labels(texts)


def _heuristic_labels(texts: list[str]) -> list[int]:
    signatures = [_signature(text) for text in texts]
    by_sig: dict[str, int] = {}
    labels: list[int] = []
    for sig in signatures:
        if sig not in by_sig:
            by_sig[sig] = len(by_sig)
        labels.append(by_sig[sig])
    return labels


def _signature(text: str) -> str:
    tokens = _tokens(text)
    if not tokens:
        return "general"
    counts = Counter(tokens)
    return counts.most_common(1)[0][0]


def _normalize_labels(raw: list[int], texts: list[str]) -> list[int]:
    buckets: dict[int, list[int]] = defaultdict(list)
    labels: list[int] = []
    for idx, label in enumerate(raw):
        if label == -1:
            labels.append(-1)
        else:
            labels.append(int(label))
            buckets[int(label)].append(idx)
    ordered = sorted(buckets, key=lambda label: min(buckets[label]))
    mapping = {label: idx for idx, label in enumerate(ordered)}
    next_label = len(mapping)
    normalized: list[int] = []
    noise_mapping: dict[str, int] = {}
    for idx, label in enumerate(labels):
        if label != -1:
            normalized.append(mapping[label])
            continue
        sig = _signature(texts[idx])
        if sig not in noise_mapping:
            noise_mapping[sig] = next_label
            next_label += 1
        normalized.append(noise_mapping[sig])
    return normalized
