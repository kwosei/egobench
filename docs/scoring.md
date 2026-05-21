# EgoBench Scoring

This document explains how EgoBench scores a model during `egobench eval` and how those scores are aggregated into a final EgoScore.

## Individual task scores

Each benchmark task is scored by the judge model on an **integer scale from 1 to 10**.

The judge receives three inputs:

- The original task prompt
- The checklist of expected outcomes generated during `egobench build`
- The candidate model's response

It returns a JSON object with `score`, `strengths`, `weaknesses`, and `rationale`. The score is hard-clamped to `[1, 10]` regardless of what the judge outputs. If the judge response cannot be parsed, the task falls back to a neutral score of **5**.

| Value | Meaning |
| --- | --- |
| 1 | Minimum — response completely fails the task |
| 5 | Neutral fallback when the judge output is unparseable |
| 10 | Maximum — response fully satisfies the checklist |

These per-task results are written to `scores.jsonl` and `rationales.jsonl` inside the run directory.

## Aggregated EgoScore

After all tasks are scored, the eval summary (`summary.json`) computes two aggregate scores.

### Raw EgoScore

A simple arithmetic mean over all task scores:

```
raw = sum(scores) / num_tasks
```

### Frequency-weighted EgoScore

Each benchmark task carries a `cluster_size` — the number of near-duplicate variants of that task that existed in your original chat history before sampling suppressed them. A task with a high `cluster_size` represents a type of request you made repeatedly, so it gets more weight in the final score.

```
frequency_weighted = sum(score × cluster_size) / sum(cluster_sizes)
```

This means a model that performs well on common recurring task types scores higher than one that performs well only on rare tasks. **Frequency-weighted EgoScore is the primary metric** used for leaderboard ranking.

Example with two tasks:

| Task | Score | cluster_size | Contribution |
| --- | --- | --- | --- |
| Python sorting (appeared 3 times) | 10 | 3 | 30 |
| French grammar (appeared 1 time) | 4 | 1 | 4 |

- Raw EgoScore: `(10 + 4) / 2 = 7.0`
- Frequency-weighted EgoScore: `(30 + 4) / (3 + 1) = 8.5`

### Per-category scores

The summary also breaks scores down by task category (for example, `Code`, `Writing`, `Reasoning`). Each category score is the mean of all task scores within that category. These are reported in the `per_category` field of `summary.json` and displayed in the radar chart in `report.html`.

## Score range summary

| Metric | Min | Max | Type |
| --- | --- | --- | --- |
| Individual task score | 1 | 10 | Integer |
| Raw EgoScore | 1.0 | 10.0 | Float (4 dp) |
| Frequency-weighted EgoScore | 1.0 | 10.0 | Float (4 dp) |
| Per-category score | 1.0 | 10.0 | Float (4 dp) |

Charts in `report.html` normalize scores to a 0–1 scale by dividing by 10 for display purposes only; the stored values are always on the 1–10 scale.
