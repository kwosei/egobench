# EgoBench Scoring

This document explains how EgoBench scores a model during `egobench eval` and how those scores are aggregated into a final EgoScore.

## Individual task scores

Each benchmark task is scored on an **integer scale from 1 to 10** by one or more judge models (see [Judge panel](#judge-panel)).

Each judge receives three inputs:

- The original task prompt
- The checklist of expected outcomes generated during `egobench build`
- The candidate model's response

Each judge returns a JSON object with `score`, `strengths`, `weaknesses`, and `rationale`. The score is hard-clamped to `[1, 10]` regardless of what the judge outputs. If a judge's response cannot be parsed it is dropped from the panel aggregate; only if *every* judge fails does the task fall back to a neutral score of **5**.

| Value | Meaning |
| --- | --- |
| 1 | Minimum — response completely fails the task |
| 5 | Neutral fallback when the judge output is unparseable |
| 10 | Maximum — response fully satisfies the checklist |

These per-task results are written to `scores.jsonl` and `rationales.jsonl` inside the run directory.

## Judge panel

By default `eval` scores with the single `[judges.default]` model. To reduce single-model bias — especially when scoring a frontier model, where you want *other* frontier models grading it — you can score with a **panel** of judges and aggregate their scores into one consensus score per task.

Configure a panel in `egobench.toml`:

```toml
[judges]
scoring_aggregate = "mean"          # "mean" (default) or "median"
exclude_candidate_provider = true   # never let a model grade itself

[[judges.scoring_panel]]
provider = "anthropic"
model = "claude-opus-4-7"

[[judges.scoring_panel]]
provider = "openai"
model = "gpt-5"
```

…or pass judges ad hoc on the command line (repeat `--judge`, which overrides the configured panel):

```
egobench eval --provider anthropic --model claude-opus-4-7 \
  --judge openai:gpt-5 --judge google:gemini-2.5-pro
```

**Aggregation.** Each judge scores every task independently; the per-task **consensus score** is the `mean` (default) or `median` of the contributing judges. Because it is an average, the consensus score is a float (e.g. `7.5`) even though each judge emits an integer. This consensus score is what feeds the aggregate EgoScores below — the rest of the pipeline is unchanged.

**Spread.** Each task records `judge_spread` (the max − min of the contributing judges' scores) so you can see where judges disagree. A high spread means the judges did not agree on how well the response did.

**Self-judging.** Set `exclude_candidate_provider = true` to drop any panel judge that shares the benchmarked model's provider, so a model never grades itself. If that would leave no judges, `eval` errors rather than silently self-judging. (An explicit `--judge` panel is always used as given.)

**Failed judges.** A judge whose output cannot be parsed is excluded from the aggregate; the neutral fallback of **5** applies only when every judge on the panel fails.

Per-judge scores and rationales are persisted in `scores.jsonl` (`judge_scores`, `judge_spread`) and `rationales.jsonl` (`judges`), and shown per task in the drill-down of `report.html`. `summary.json` records the panel under `judges` and the method under `scoring_aggregate`.

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
| Individual judge score | 1 | 10 | Integer |
| Consensus task score | 1.0 | 10.0 | Float (mean/median of judges) |
| Raw EgoScore | 1.0 | 10.0 | Float (4 dp) |
| Frequency-weighted EgoScore | 1.0 | 10.0 | Float (4 dp) |
| Per-category score | 1.0 | 10.0 | Float (4 dp) |

Charts in `report.html` normalize scores to a 0–1 scale by dividing by 10 for display purposes only; the stored values are always on the 1–10 scale.
