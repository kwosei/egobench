# EgoBench

EgoBench is a local CLI that turns exported chat history into a personal LLM benchmark. It ingests ChatGPT, Claude, or generic JSONL exports, builds a versioned benchmark with per-task checklists, evaluates candidate models against it, and renders local HTML/Markdown reports.

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
- `[judges.default]`: the judge used to label clusters, merge checklists, and score eval answers.
- `[[judges.checklist_panel]]`: one or more models that draft checklist items during build phase 7.
- `[embeddings]`: the provider/model used to embed conversations for clustering in build phase 3.
- `[sample]`: benchmark size and long-tail sampling behavior. `target_n` is clamped to 1-200.

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

Build-time API calls come from embeddings, cluster categorization, checklist generation, and checklist merging. Local embedding servers do not add API cost.

### 7. Build the benchmark

```bash
uv run egobench build --yes
```

`build` turns ingested conversations into `egobench-workspace/benchmark.json`. It runs these phases:

| Phase | Name | API use | What it does |
| --- | --- | --- | --- |
| 2 | Drop non-tasks | No | Removes greetings, acknowledgments, empty prompts, and test pings. |
| 3 | Embed and cluster | Embeddings | Groups similar first-user turns. Cluster size becomes a frequency signal. |
| 4 | Categorize | Judge | Labels each cluster from samples of your actual tasks. |
| 5 | Importance scoring | No | Converts cluster frequency into an importance score. |
| 6 | Stratified sample | No | Selects `sample.target_n` tasks while preserving common and rare categories. |
| 7 | Checklist generation | Panel and judge | Creates a 5-10 item rubric for every selected task. |
| 8 | Lock and version | No | Writes `benchmark.json`, `benchmark_vN.json`, and a reproducible hash. |

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

The candidate model answers each task in the current `benchmark.json`. The judge scores those answers with the task checklist. Each eval writes a run directory under `egobench-workspace/runs/` and updates the local report.

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

- `leaderboard` prints local runs ranked by EgoScore.
- `report` regenerates `report.html` and `report.md` from existing runs.
- `cost` summarizes the local cost ledger by phase and model.
- `report.html` is the easiest way to inspect scores, categories, and run details.

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
