from __future__ import annotations

import hashlib
import math
import re
from collections import Counter, defaultdict

import numpy as np

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


def run(db: DB, cfg: EgoBenchConfig) -> dict:
    tasks = _task_rows(db)
    if not tasks:
        return {"phase": 3, "clusters": 0}
    texts = [row["first_user_text"] for row in tasks]
    embeddings = _embed_texts(texts, cfg, db)
    labels = _cluster(embeddings, [row["first_user_text"] for row in tasks], cfg)
    sizes = Counter(labels)
    with db.connect() as conn:
        conn.executemany(
            """
            UPDATE task_candidates
            SET cluster_id = ?, cluster_size = ?, updated_at = CURRENT_TIMESTAMP
            WHERE conversation_id = ?
            """,
            [
                (int(labels[idx]), int(sizes[labels[idx]]), tasks[idx]["conversation_id"])
                for idx in range(len(tasks))
            ],
        )
    return {"phase": 3, "clusters": len(sizes), "tasks": len(tasks)}


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


def _embed_texts(texts: list[str], cfg: EgoBenchConfig, db: DB) -> list[list[float]]:
    if cfg.embeddings.backend == "openai" and cfg.api_key_for_provider("openai"):
        try:
            from openai import OpenAI

            model = cfg.embeddings.model
            client = OpenAI(api_key=cfg.api_key_for_provider("openai"))
            response = client.embeddings.create(model=model, input=texts)
            vectors = [item.embedding for item in response.data]
            usage = getattr(response, "usage", None)
            input_tokens = getattr(usage, "prompt_tokens", sum(estimate_tokens(text) for text in texts))
            with db.connect() as conn:
                conn.execute(
                    """
                    INSERT INTO phase_cost_log(phase, model, input_tokens, output_tokens, cost_usd)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    ("phase3", model, int(input_tokens), 0, estimate_cost(model, int(input_tokens), 0)),
                )
            return vectors
        except Exception:
            pass
    if cfg.embeddings.backend == "local":
        local_vectors = _local_sentence_transformer(texts, cfg.embeddings.model)
        if local_vectors is not None:
            return local_vectors
    return [_embed_text(text) for text in texts]


def _local_sentence_transformer(texts: list[str], model: str) -> list[list[float]] | None:
    try:
        from sentence_transformers import SentenceTransformer

        encoder = SentenceTransformer(model)
        vectors = encoder.encode(texts, normalize_embeddings=True)
        return [list(map(float, vector)) for vector in vectors]
    except Exception:
        return None


def _tokens(text: str) -> list[str]:
    return [
        token.lower()
        for token in re.findall(r"[a-zA-Z][a-zA-Z0-9_+-]{2,}", text)
        if token.lower() not in STOP
    ]


def _cluster(embeddings: list[list[float]], texts: list[str], cfg: EgoBenchConfig) -> list[int]:
    if len(embeddings) < 3:
        return _heuristic_labels(texts)
    try:
        import hdbscan  # type: ignore

        min_cluster_size = max(3, len(embeddings) // 50)
        raw = hdbscan.HDBSCAN(min_cluster_size=min_cluster_size, prediction_data=False).fit_predict(np.array(embeddings))
        if len(set(raw)) > 1:
            return _normalize_labels(raw.tolist(), texts)
    except Exception:
        pass
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
