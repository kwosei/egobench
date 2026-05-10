# EgoBench

EgoBench is a local CLI that turns exported chat history into a personal LLM benchmark. It ingests ChatGPT, Claude, or generic JSONL exports, builds a versioned benchmark with per-task checklists, evaluates candidate models, and renders local reports.

```bash
uv run egobench init
uv run egobench ingest tests/fixtures/chatgpt_export_sample.json --adapter chatgpt
uv run egobench build --yes
uv run egobench eval --model local-demo --yes
uv run egobench leaderboard
```

All project state is written under `./egobench-workspace/`. API keys are resolved from environment variables or keyring pointers in `egobench.toml`; they are not written to disk.

