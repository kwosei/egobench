from __future__ import annotations

import json
import re

from egobench.config import EgoBenchConfig, ModelRef
from egobench.db import DB
from egobench.llm.factory import make_client


def judge_response(
    *,
    db: DB,
    cfg: EgoBenchConfig,
    judge_model: ModelRef,
    task_prompt: str,
    checklist: list[str],
    response: str,
) -> dict:
    client = make_client(judge_model, cfg, db, "eval-judge")
    prompt = (
        "Return JUDGE_SCORE_JSON with keys score, strengths, weaknesses, rationale. "
        "Score must be an integer from 1 to 10.\n"
        f"<TASK>\n{task_prompt}\n</TASK>\n"
        f"<CHECKLIST>\n{json.dumps(checklist, ensure_ascii=False)}\n</CHECKLIST>\n"
        f"<RESPONSE>\n{response}\n</RESPONSE>"
    )
    try:
        payload = _json_object(client.complete(prompt).text)
    except Exception:
        payload = {
            "score": 5,
            "strengths": [],
            "weaknesses": ["Judge response could not be parsed"],
            "rationale": "Fell back to neutral score because the judge output was not valid JSON.",
        }
    score = int(payload.get("score", 5))
    return {
        "score": max(1, min(10, score)),
        "strengths": [str(item) for item in payload.get("strengths", [])],
        "weaknesses": [str(item) for item in payload.get("weaknesses", [])],
        "rationale": str(payload.get("rationale", "")),
    }


def _json_object(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))

