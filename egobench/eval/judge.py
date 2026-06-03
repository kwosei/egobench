from __future__ import annotations

import json
import statistics
from concurrent.futures import ThreadPoolExecutor

from egobench.config import EgoBenchConfig, ModelRef
from egobench.db import DB
from egobench.llm.factory import make_client
from egobench.llm.pricing import PricingResolver
from egobench.pipeline.json_utils import parse_json_object as _json_object


# Panels are small (a handful of frontier judges), so a modest cap is plenty.
MAX_JUDGE_WORKERS = 8


def judge_response(
    *,
    db: DB,
    cfg: EgoBenchConfig,
    judge_model: ModelRef,
    task_prompt: str,
    checklist: list[str],
    response: str,
    pricing: PricingResolver | None = None,
) -> dict:
    """Score one response with one judge.

    Returns ``score`` (clamped to [1, 10]), ``strengths``, ``weaknesses``,
    ``rationale``, and ``ok`` — ``ok`` is ``False`` when the judge output could
    not be parsed into a valid score, so a panel can drop it from the aggregate
    rather than dragging the consensus toward the neutral fallback of 5.
    """
    client = make_client(judge_model, cfg, db, "eval-judge", pricing=pricing)
    prompt = (
        "Return JUDGE_SCORE_JSON with keys score, strengths, weaknesses, rationale. "
        "Score must be an integer from 1 to 10.\n"
        f"<TASK>\n{task_prompt}\n</TASK>\n"
        f"<CHECKLIST>\n{json.dumps(checklist, ensure_ascii=False)}\n</CHECKLIST>\n"
        f"<RESPONSE>\n{response}\n</RESPONSE>"
    )
    ok = True
    try:
        payload = _json_object(client.complete(prompt).text)
    except Exception:
        ok = False
        payload = {}
    try:
        score = int(payload.get("score"))
    except (TypeError, ValueError):
        ok = False
        score = 5
    strengths = [str(item) for item in payload.get("strengths", [])]
    weaknesses = [str(item) for item in payload.get("weaknesses", [])]
    rationale = str(payload.get("rationale", ""))
    if not ok and not weaknesses:
        weaknesses = ["Judge response could not be parsed"]
        if not rationale:
            rationale = "Fell back to neutral score because the judge output was not valid JSON."
    return {
        "score": max(1, min(10, score)),
        "strengths": strengths,
        "weaknesses": weaknesses,
        "rationale": rationale,
        "ok": ok,
    }


def aggregate_scores(scores: list[float], method: str = "mean") -> float:
    """Combine a panel's per-judge scores into one consensus score.

    ``mean`` matches the rest of EgoScore's averaging; ``median`` resists a
    single rogue judge. An empty list returns the neutral fallback of 5.
    """
    if not scores:
        return 5.0
    if method == "median":
        return float(statistics.median(scores))
    return float(statistics.mean(scores))


def judge_response_panel(
    *,
    db: DB,
    cfg: EgoBenchConfig,
    judge_models: list[ModelRef],
    task_prompt: str,
    checklist: list[str],
    response: str,
    aggregate: str = "mean",
    pricing: PricingResolver | None = None,
) -> dict:
    """Score one response with a panel of judges and aggregate the result.

    Each judge scores independently (in parallel); their scores are combined
    into a single consensus ``score``. Judges whose output failed to parse are
    excluded from the aggregate; the neutral fallback of 5 only applies when
    every judge failed. Per-judge detail and the score ``spread`` (max − min of
    the contributing judges) are returned for transparency.
    """
    judge_models = _unique_judge_models(judge_models)

    def _judge_one(ref: ModelRef) -> dict:
        kwargs = {
            "db": db,
            "cfg": cfg,
            "judge_model": ref,
            "task_prompt": task_prompt,
            "checklist": checklist,
            "response": response,
        }
        if pricing is not None:
            kwargs["pricing"] = pricing
        return judge_response(**kwargs)

    # Iterate futures in submission order so the persisted output is
    # deterministic regardless of which judge returns first.
    with ThreadPoolExecutor(max_workers=min(len(judge_models), MAX_JUDGE_WORKERS)) as executor:
        futures = [executor.submit(_judge_one, ref) for ref in judge_models]
        judged = [future.result() for future in futures]

    detail = [
        {
            "judge": ref.display(),
            "score": result["score"],
            "strengths": result["strengths"],
            "weaknesses": result["weaknesses"],
            "rationale": result["rationale"],
            "ok": result["ok"],
        }
        for ref, result in zip(judge_models, judged)
    ]
    contributing = [result["score"] for result in judged if result["ok"]]
    used = contributing or [result["score"] for result in judged]
    spread = (max(used) - min(used)) if used else 0
    return {
        "score": round(aggregate_scores(used, aggregate), 4),
        "judge_scores": {entry["judge"]: entry["score"] for entry in detail},
        "judge_spread": spread,
        "aggregate": aggregate,
        "judges": detail,
    }


def _unique_judge_models(judge_models: list[ModelRef]) -> list[ModelRef]:
    unique: list[ModelRef] = []
    seen: set[tuple[str, str]] = set()
    for ref in judge_models:
        key = (ref.provider, ref.model)
        if key in seen:
            continue
        seen.add(key)
        unique.append(ref)
    return unique
