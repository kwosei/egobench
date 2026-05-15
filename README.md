# EgoBench

EgoBench is a local CLI that turns your exported chat history into a personal LLM benchmark. It ingests ChatGPT, Claude, or generic JSONL exports, builds a versioned benchmark with per-task checklists, evaluates candidate models against it, and renders local HTML/Markdown reports.

All project state (the database, benchmark JSON, run artifacts, reports) lives under `./egobench-workspace/` in the directory you run the CLI from.

## Contents

- [Setup: API keys](#setup-api-keys)
- [Choosing which APIs get called](#choosing-which-apis-get-called)
- [Using OpenRouter (or another OpenAI-compatible gateway)](#using-openrouter-or-another-openai-compatible-gateway)
- [Local / self-hosted embeddings](#local--self-hosted-embeddings)
- [Pipeline phases](#pipeline-phases)
- [Commands](#commands)
- [A typical workflow](#a-typical-workflow)
- [Where things live](#where-things-live)

## Setup: API keys

EgoBench calls LLM APIs during `build` (to generate checklists, etc.) and `eval` (to run candidates + judges). You need to provide credentials for any provider you plan to use.

.env.example gives you a list of API keys and config options.


## Setup: Init and Configs

Once you run egobench init, a file called `egobench-workspace/egobench.toml` is created. This is your config file. It contains extensive documentation for the various knobs in this project. You use it to do things like:
- Set what model providers you want to support
- Set which models you want to use at each step in that file


## Pipeline phases

`egobench build` runs eight phases. Each phase persists its output to `egobench.db` (and a JSON snapshot under `egobench-workspace/cache/`) so a later run with `--from N` can resume cheaply.

### At a glance

| # | Phase | API? | What it does |
| --- | --- | :---: | --- |
| 1 | Ingest & normalize | — | Load export → common schema (run by `egobench ingest`, not `build`) |
| 2 | Drop non-tasks | — | Strip greetings, acks, test pings — never filter on "quality" |
| 3 | Embed & cluster | ✅ embed | Cluster similar conversations; cluster size = frequency weight |
| 4 | Categorize | ✅ judge × clusters | Bottom-up category labels per cluster |
| 5 | Importance scoring | — | `log1p(cluster_size)` → importance ∈ [0, 1] |
| 6 | Stratified sample | — | Pick `target_n` tasks, preserve category mix, oversample rare ones |
| 7 | Checklist generation | ✅ panel × tasks + merge | 5–10 item rubric per task, multi-judge + merge |
| 8 | Lock & version | — | Hash + version → `benchmark.json` |

<details>
<summary><strong>Per-phase detail</strong></summary>

#### Phase 1 — Ingest & normalize
- **Code:** `egobench/pipeline/phase1_ingest.py` (invoked by `egobench ingest`, not `build`)
- **Does:** Loads a ChatGPT, Claude, or JSONL export and normalizes every conversation to a common schema (`conversation_id`, `turns`, `timestamps`, `model_used`, `metadata`).
- **Why:** Every downstream phase assumes one shape regardless of where the chat history came from. Centralizing format quirks here keeps the rest of the pipeline format-agnostic.

#### Phase 2 — Drop non-tasks
- **Code:** `egobench/pipeline/phase2_drop_nontasks.py`
- **Does:** Filters conversations whose first user turn is a greeting (`"hi"`), acknowledgment (`"thanks"`), test ping (`"are you there"`), or empty.
- **Why:** These conversations have no extractable task. We deliberately do **not** filter on perceived "quality" or triviality: short casual queries (`"should I order pizza or thai"`) are real usage and belong in the benchmark, and long pastes (stack traces, transcripts) probe long-context behavior and are often the most valuable signal. Quality and importance judgments happen in phase 5, where you can see and override them.

#### Phase 3 — Embed & cluster
- **Code:** `egobench/pipeline/phase3_embed_cluster.py`
- **Does:** Embeds the first user turn of each surviving conversation, then clusters with HDBSCAN (falling back to a token-signature heuristic when HDBSCAN is unavailable or finds nothing). Cluster size is stored on each row.
- **Why:** Clustering surfaces the user's recurring task *types* and gives every task a frequency weight, which later powers stratified sampling (phase 6) and the frequency-weighted EgoScore.

#### Phase 4 — Categorize
- **Code:** `egobench/pipeline/phase4_categorize.py`
- **Does:** Sends up to five sample tasks from each cluster to the judge model and asks for a short label and description. Categories like "Python debugging" or "Recipe ideas" emerge from your data, not a fixed list.
- **Why:** Bottom-up categories reflect the user's actual mix. A fixed taxonomy would either over-collapse niche use cases or include categories the user never touches. Categories are the unit per-category reporting and radar charts roll up into.

#### Phase 5 — Importance scoring
- **Code:** `egobench/pipeline/phase5_importance.py`
- **Does:** Assigns each candidate task an importance score proxied from `log1p(cluster_size)`, normalized to `[0, 1]`. Overridable in `egobench review`.
- **Why:** A recurrent activity matters more than a one-off. We deliberately do **not** predict task *difficulty* — LLM-judged difficulty is circular (the thing being measured), noisy, and unstable across model generations. Frequency is a stable, defensible proxy for "this matters to me."

#### Phase 6 — Stratified sample
- **Code:** `egobench/pipeline/phase6_sample.py`
- **Does:** Picks `sample.target_n` tasks (default 100), allocating quota across categories using `(category_size) ** oversample_alpha` so common categories dominate but rare ones still get representation. Within each category, higher-importance tasks are preferred.
- **Why:** A flat random sample would over-represent your one giant cluster and starve everything else. Strict proportional sampling would lose the long tail entirely. The oversample exponent (default 0.8) is the knob between these extremes.

#### Phase 7 — Checklist generation
- **Code:** `egobench/pipeline/phase7_checklist.py`
- **Does:** For each sampled task, asks every model in `judges.checklist_panel` for a 5–10 item rubric, then asks the default judge to deduplicate and merge.
- **Why:** The checklist is the rubric the judge uses during `eval`. Generating it once at build time (vs. on every eval run) keeps eval fast and reproducible, and using a panel + merge reduces single-model bias in what counts as "doing well" on the task.

#### Phase 8 — Lock & version
- **Code:** `egobench/pipeline/phase8_lock.py`
- **Does:** Freezes the selected tasks + checklists into `benchmark.json` with a version number and content hash.
- **Why:** Eval results have to be reproducible and comparable across model runs. Hashing the benchmark lets you tell whether two run directories were scored against the same task set; versioning lets edits via `egobench review` create a new benchmark without losing the previous one.
</details>

> [!TIP]
> **Resuming.** Phase outputs are content-addressed: each phase's cache key is a hash of its inputs plus the config. Re-running `build` with no changes prints `Skipping phaseN; cache key matched.` and exits instantly. Change a judge model or `sample.target_n` and the affected phases (and everything downstream) re-run. Use `--from N` to force a re-run from phase `N` after a transient API failure.

## Commands

| Command | Costs $? | Purpose |
| --- | :---: | --- |
| `egobench init` | — | Create workspace, default config, SQLite DB |
| `egobench ingest <path>` | — | Import a chat export |
| `egobench build` | 💰 | Run phases 1–8 → `benchmark.json` |
| `egobench refresh` | 💰 | Shortcut for `build --from 2` |
| `egobench review` | — | Interactive TUI to inspect/edit the benchmark |
| `egobench eval --model <name>` | 💰 | Score one candidate model against the benchmark |
| `egobench leaderboard` | — | Print a local leaderboard across runs |
| `egobench report` | — | Regenerate `report.html` / `report.md` |
| `egobench cost` | — | Summarize the cost ledger |

<details>
<summary><strong>Flags and details for each command</strong></summary>

#### `egobench init`
Creates `./egobench-workspace/`, writes a default `egobench.toml`, and initializes the SQLite database. Safe to re-run — it won't overwrite an existing config.

#### `egobench ingest <path> [--adapter auto|chatgpt|claude|jsonl]`
Imports a chat export into the database. `--adapter auto` sniffs the format. Run this once per export file.

#### `egobench build [--from N] [--estimate-only] [--yes]`
Runs the full build pipeline (phases 1–8) over your ingested conversations to produce `benchmark.json`. Phases 2–7 call LLMs and cost money.

- `--estimate-only` prints the projected cost table and exits without calling any APIs
- `--yes` / `-y` skips the "Continue with paid phases?" interactive confirmation (use this in scripts/CI)
- `--from N` resumes from a specific phase (useful for re-running after a failure)

#### `egobench refresh [--yes]`
Shortcut for `build --from 2`. Use it when you've changed config but not added new conversations.

#### `egobench review`
Opens an interactive Textual UI to inspect and edit the benchmark before evaluating.

#### `egobench eval --model <name> [--judge <name>] [--estimate-only] [--yes]`
Runs one candidate model against the current `benchmark.json`, scored by the judge (defaults to `judges.default`). Writes a run directory under `egobench-workspace/runs/` and updates the local report.

#### `egobench leaderboard`
Prints a local leaderboard table across all runs.

#### `egobench report`
Regenerates `report.html` and `report.md` from existing run data without re-evaluating.

#### `egobench cost`
Summarizes the cost ledger — what's been spent per phase and model so far.
</details>

## A typical workflow

```bash
# 1. One-time setup
uv run egobench init
echo 'ANTHROPIC_API_KEY=sk-ant-...' > .env

# 2. Pull in your exports (do this every time you have new history)
uv run egobench ingest ~/Downloads/chatgpt-export.json --adapter chatgpt
uv run egobench ingest ~/Downloads/claude-export.json --adapter claude

# 3. Preview costs before paying
uv run egobench build --estimate-only

# 4. Build the benchmark (costs money)
uv run egobench build --yes

# 5. Evaluate candidate models
uv run egobench eval --provider anthropic --model claude-opus-4-7 --yes
uv run egobench eval --model claude-sonnet-4-6 --yes

# 6. Compare
uv run egobench leaderboard
open egobench-workspace/report.html
```

## Where things live

```
./
├── .env                          # your keys (gitignored)
├── egobench-workspace/
│   ├── egobench.toml             # config — edit this to change models
│   ├── egobench.db               # SQLite: conversations, costs, runs
│   ├── benchmark.json            # the built benchmark
│   ├── runs/                     # one directory per eval run
│   ├── report.html
│   └── report.md
```

API keys are never written to `egobench-workspace/` — they're only ever resolved at runtime from env vars or keyring.
