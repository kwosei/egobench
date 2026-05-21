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

### 4. Configure models and sampling

Open `egobench-workspace/egobench.toml` after `init` to configure what models to use for various phases and how egobench should sample from your chats.

### 5. Ingest your chat exports

```bash
uv run egobench ingest ~/Downloads/chatgpt-export.json --adapter chatgpt
uv run egobench ingest ~/Downloads/claude-export.json --adapter claude
uv run egobench ingest ~/Downloads/tasks.jsonl --adapter jsonl
```

Use `--adapter auto` when you want EgoBench to detect the format. Ingestion normalizes conversations into the local SQLite database and does not call any model APIs.

JSONL input should contain one conversation per line with an `id` and `turns`, where each turn has a `role` and either `text` or `content`.

### 6. Build the benchmark

```bash
uv run egobench build
```

`build` turns ingested conversations into `egobench-workspace/benchmark.json`.

For a detailed phase-by-phase map of what runs, which model is used, and what each step writes, see [EgoBench Pipeline Phases](docs/pipeline-phases.md).

Phase outputs are cached by inputs and config. Re-running `build` with no relevant changes skips cached phases. Use `--from N` to force phase `N` and downstream phases after a failed or intentionally changed run:

```bash
uv run egobench build --from 7 --yes
```

Use `refresh` when you changed config but did not add new conversations:

```bash
uv run egobench refresh --yes
```

`refresh` is a shortcut for `build --from 2`.

### 7. Review the benchmark

```bash
uv run egobench review
```

The review UI lets you inspect selected tasks before evaluating models. Use it after `build` when you want to edit checklist items or tune importance values.

### 8. Evaluate a model

e.g. eval your benchmark against Google Gemma 4 running locally via LM Studio.

```bash
uv run egobench eval --provider lmstudio --model google/gemma-4-e4b
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

egobench-workspace is intentionally local and portable. Delete it to start over, copy it to preserve a benchmark, or keep multiple workspaces by running the CLI from different directories.
