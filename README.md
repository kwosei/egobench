# EgoBench

EgoBench is a local CLI that turns exported chat history into a personal LLM benchmark. It ingests ChatGPT, Claude, or generic JSONL exports, discovers recurring task families from your actual chats, builds a versioned benchmark with per-task checklists, evaluates candidate models against it, and renders local HTML/Markdown reports.

All project state lives under `./egobench-workspace/` in the directory where you run the CLI. API keys are loaded from your environment or `.env`; they are not written into the workspace.

## How to use

### 1. Install and check the CLI

From this repository:

```bash
uv sync
uv run egobench --help
```

If you install EgoBench as a package, you can use `egobench ...` directly instead of `uv run egobench ...`.

### 2. Create a workspace

```bash
uv run egobench init
```

This creates:

- `egobench-workspace/egobench.toml` for model, provider, embedding, and sampling settings
- `egobench-workspace/egobench.db` for ingested conversations, build state, cost logs, and runs
- `egobench-workspace/cache/` and `egobench-workspace/runs/` for generated artifacts

`init` is safe to re-run. It will not overwrite an existing config unless you pass `--force`.

### 3. Add API keys

```bash
cp .env.example .env
$EDITOR .env
```

You only need keys for providers you actually use in `egobench.toml` or in `egobench eval`.

The CLI auto-loads `.env` on startup, and real shell environment variables take precedence. If a provider declares `api_key_env` but the key is missing, EgoBench falls back to a deterministic recorded client. That is useful for offline smoke tests, but it means you are not calling the real model.

### 4. Configure models and sampling

Open `egobench-workspace/egobench.toml` after `init`.

Every model reference has two parts:

```toml
provider = "anthropic"
model = "claude-opus-4-7"
```

Providers are declared once:

```toml
[providers.anthropic]
api_key_env = "ANTHROPIC_API_KEY"
base_url = "https://api.anthropic.com/v1/"

[providers.openai]
api_key_env = "OPENAI_API_KEY"
```

The main config sections are:

- `[providers.*]`: named OpenAI-compatible endpoints. Use this for Anthropic, OpenAI, OpenRouter, LM Studio, Ollama, or another gateway.
- `[filter]`: a cheap fast model used in build phase 2 to classify conversations as genuine tasks. Defaults to `anthropic:claude-haiku-4-5-20251001`. Uses concurrent requests for speed.
- `[judges.default]`: the judge used to infer task families, annotate benchmark candidates, merge checklists, and score eval answers.
- `[[judges.checklist_panel]]`: one or more models that draft checklist items during build phase 7.
- `[embeddings]`: the provider/model used to embed conversations for similarity grouping in build phase 3.
- `[sample]`: benchmark size, near-duplicate suppression, family caps, and long-tail sampling behavior. `target_n` is clamped to 1-200.

The default sampler config is:

```toml
[sample]
target_n = 100
max_family_tasks = 5
near_duplicate_threshold = 0.90
long_tail_fraction = 0.20
oversample_alpha = 0.8
```

`oversample_alpha` is kept for compatibility with older configs. The current sampler is family-aware and is driven primarily by `max_family_tasks`, `near_duplicate_threshold`, and `long_tail_fraction`.

To use OpenRouter or another OpenAI-compatible gateway, add a provider and reference its gateway-specific model IDs:

```toml
[providers.openrouter]
api_key_env = "OPENROUTER_API_KEY"
base_url = "https://openrouter.ai/api/v1"

[judges.default]
provider = "openrouter"
model = "anthropic/claude-opus-4"
```

To keep embeddings local, point `[embeddings]` at a local OpenAI-compatible server:

```toml
[providers.lmstudio]
base_url = "http://localhost:1234/v1"

[embeddings]
provider = "lmstudio"
model = "nomic-embed-text-v1.5"
```

Local providers usually omit `api_key_env`; the SDK still receives a placeholder key internally because local servers commonly ignore auth.

### 5. Ingest your chat exports

```bash
uv run egobench ingest ~/Downloads/chatgpt-export.json --adapter chatgpt
uv run egobench ingest ~/Downloads/claude-export.json --adapter claude
uv run egobench ingest ~/Downloads/tasks.jsonl --adapter jsonl
```

Use `--adapter auto` when you want EgoBench to detect the format. Ingestion normalizes conversations into the local SQLite database and does not call any model APIs.

JSONL input should contain one conversation per line with an `id` and `turns`, where each turn has a `role` and either `text` or `content`.

### 6. Estimate build cost

```bash
uv run egobench build --estimate-only
```

This prints the projected API cost without calling paid APIs. The estimate is based on your current config and ingested conversation count.

Build-time API calls come from embeddings, task-family inference, task annotation, checklist generation, and checklist merging. Local embedding servers do not add API cost.

### 7. Build the benchmark

```bash
uv run egobench build --yes
```

`build` turns ingested conversations into `egobench-workspace/benchmark.json`. EgoBench does not use a fixed topical taxonomy. Phase 2 runs an explicit LLM-based filter using a cheap configurable model (default: `claude-haiku-4-5-20251001`) to drop anything that is not a genuine task before anything else runs. Remaining conversations then go through embeddings for two mechanical pre-passes: rough batching groups for judge context and near-duplicate groups for later suppression. The actual benchmark family system is produced later by the judge as open-ended semantic metadata such as `task_family`, `domain`, and `skills`.

Because LLM-generated labels are free-form, phase 4 canonicalizes related labels before later phases use them. The judge annotates task batches from each embedding-based candidate group, returning both group context and per-task family, difficulty, and specificity metadata. All unique family label strings are then sent back to the judge in chunks of 120 to map synonymous labels onto canonical families. For example, labels like `French grammar distinction explanation` and `French grammar nuance explanation` can be merged into one canonical family such as `French grammar explanation` with a stable `task_family_id`. Importance scoring and sampling group by `task_family_id`, not by raw label strings or embedding batch ids.

For a detailed phase-by-phase map of what runs, which model is used, and what each step writes, see [EgoBench Pipeline Phases](docs/pipeline-phases.md).

It runs these phases:

| Phase | Name | API use | What it does |
| --- | --- | --- | --- |
| 2 | Drop non-tasks | Filter model | Uses a cheap fast model to classify every conversation as a genuine task (YES) or not (NO). Obvious non-tasks (greetings, acks, pings) are dropped by heuristic before any API call. Remaining candidates are classified concurrently. |
| 3 | Embed and pre-group | Embeddings | Creates rough batching groups for judge context and near-duplicate groups for repeated variants. These are mechanical helpers, not final benchmark families. |
| 4 | Judge family inference | Judge | Annotates task batches with open-ended family metadata, then maps synonymous raw labels into canonical `task_family_id` families while also assigning difficulty and specificity. |
| 5 | Family importance | No | Scores each family from frequency, duplicate diversity, and difficulty mix. |
| 6 | Family-aware sample | No | Selects `sample.target_n` tasks, caps dominant families, suppresses near-duplicates, and reserves long-tail coverage. |
| 7 | Checklist generation | Panel and judge | Creates 5-10 item rubrics for selected tasks using batched panel and merge calls with family metadata as context. |
| 8 | Lock and version | No | Writes `benchmark.json`, `benchmark_vN.json`, and a reproducible hash. |

After phase 4, the terminal summary reports how many families were inferred and the largest families. After phase 6, it reports the selected task count, selected family count, top selected families, and duplicate variants suppressed.

Each benchmark task includes:

```json
{
  "task_family_id": "french-grammar-explanation-a1b2c3d4",
  "task_family": "French grammar explanation",
  "domain": "French language learning",
  "skills": ["grammar explanation", "translation nuance"],
  "difficulty": "medium",
  "specificity": "generalizable"
}
```

Benchmark metadata includes distribution summaries:

```json
{
  "task_count": 100,
  "task_family_count": 42,
  "domain_distribution": {},
  "family_distribution": {},
  "difficulty_distribution": {},
  "specificity_distribution": {}
}
```

Phase outputs are cached by inputs and config. Re-running `build` with no relevant changes skips cached phases. Use `--from N` to force phase `N` and downstream phases after a failed or intentionally changed run:

```bash
uv run egobench build --from 7 --yes
```

Use `refresh` when you changed config but did not add new conversations:

```bash
uv run egobench refresh --yes
```

`refresh` is a shortcut for `build --from 2`.

### 8. Review the benchmark

```bash
uv run egobench review
```

The review UI lets you inspect selected tasks before evaluating models. Use it after `build` when you want to edit checklist items or tune importance values.

### 9. Evaluate a model

Start with a cost estimate:

```bash
uv run egobench eval --provider anthropic --model claude-opus-4-7 --estimate-only
```

Then run the eval:

```bash
uv run egobench eval --provider anthropic --model claude-opus-4-7 --yes
```

The candidate model answers each task in the current `benchmark.json`. The judge scores those answers with the task checklist on a 1–10 scale. Each eval writes a run directory under `egobench-workspace/runs/` and updates the local report. For a full explanation of how individual scores are computed and aggregated into EgoScore, see [EgoBench Scoring](docs/scoring.md).

By default, eval uses `[judges.default]`. To override the judge for one run:

```bash
uv run egobench eval \
  --provider openrouter \
  --model anthropic/claude-sonnet-4 \
  --judge-provider anthropic \
  --judge-model claude-opus-4-7 \
  --yes
```

### 10. Compare and inspect results

```bash
uv run egobench leaderboard
uv run egobench report
uv run egobench cost
open egobench-workspace/report.html
```

- `leaderboard` prints local runs ranked by EgoScore (frequency-weighted; see [EgoBench Scoring](docs/scoring.md)).
- `report` regenerates `report.html` and `report.md` from existing runs.
- `cost` summarizes the local cost ledger by phase and model.
- `report.html` is the easiest way to inspect scores, task families, and run details.

## Command reference

| Command | Paid APIs | Purpose |
| --- | --- | --- |
| `egobench init [--force]` | No | Create `egobench-workspace/`, default config, and SQLite DB. |
| `egobench ingest <path> [--adapter auto|chatgpt|claude|jsonl]` | No | Import chat exports into the local DB. |
| `egobench build [--from N] [--estimate-only] [--yes]` | Yes | Build `benchmark.json` from ingested conversations. |
| `egobench refresh [--yes]` | Yes | Rebuild from phase 2 with the current config. |
| `egobench review` | No | Open the interactive benchmark review UI. |
| `egobench eval --provider <name> --model <id> [--estimate-only] [--yes]` | Yes | Score one candidate model against the benchmark. |
| `egobench leaderboard` | No | Print a local leaderboard across eval runs. |
| `egobench report` | No | Regenerate local HTML and Markdown reports. |
| `egobench cost` | No | Summarize recorded spend by phase and model. |

## Workspace layout

```text
./
|-- .env
`-- egobench-workspace/
    |-- egobench.toml
    |-- egobench.db
    |-- benchmark.json
    |-- benchmark_vN.json
    |-- cache/
    |-- runs/
    |-- report.html
    `-- report.md
```

The workspace is intentionally local and portable. Delete it to start over, copy it to preserve a benchmark, or keep multiple workspaces by running the CLI from different directories.
