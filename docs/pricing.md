# EgoBench Pricing Estimates

EgoBench estimates API spend before `build` and `eval`, then records a best-effort cost in the SQLite cost ledger after real model calls complete. These numbers are meant to help prevent surprise spend, not to replace the provider invoice.

## Resolver Order

For each model row, EgoBench resolves pricing in this order:

1. `[[pricing.models]]` overrides in `egobench-workspace/egobench.toml`
2. Cached public pricing catalogs: LiteLLM first for direct providers, OpenRouter first for the `openrouter` provider
3. A small built-in fallback table for common/default models
4. A rough family estimate for known families like `gpt-5.5`, Claude Opus 4.x, Gemini 3.x Pro, and Grok 4.x
5. `unknown`

The CLI constructs a workspace pricing resolver for `build --dry-run`, `eval --dry-run`, and real runs. Public catalog JSON is cached under:

```text
egobench-workspace/cache/pricing/
```

The cache TTL is 24 hours. If refresh fails, EgoBench falls back to the existing cache, then to built-in or rough prices. Catalog fetches are public HTTP requests; API keys are not sent to OpenRouter or LiteLLM for pricing lookup.

## Reading Estimate Tables

Estimate tables include a `Price` column:

| Label | Meaning |
| --- | --- |
| `config` | Exact user-pinned price from `egobench.toml` |
| `litellm` | Exact model id match from the LiteLLM catalog |
| `openrouter` | Exact model id match from the OpenRouter catalog |
| `builtin` | Exact built-in fallback match |
| `normalized/...` | Model id was normalized before matching, for example `gpt-5-5` -> `gpt-5.5` |
| `rough/family` | Family-level estimate, not a model-id match |
| `local` | Provider has no configured API key env/keyring, so EgoBench treats it as local or unauthenticated |
| `unknown` | No usable price was found |

Approximate rows are marked with `≈` in the `Cost` column and total. Unknown rows show `unknown`, and the total is printed as `$known + unknown`.

## Overrides

Use overrides when a model is new, you have custom pricing, a cloud provider charges a regional premium, or the public catalogs are stale.

Prices are USD per 1 million tokens:

```toml
[[pricing.models]]
provider = "openai" # optional; omit to match this model id for any provider
model = "gpt-5.5"
input_per_1m = 5.00
output_per_1m = 30.00

[[pricing.models]]
provider = "anthropic"
model = "claude-opus-4-8"
input_per_1m = 5.00
output_per_1m = 25.00
```

`input_per_1k` and `output_per_1k` are also accepted for older per-1K style price sheets.

Pricing overrides do not affect the benchmark hash or phase cache keys. They only affect dry-run estimates, confirmation panels, and cost ledger entries.

## Model Id Normalization

EgoBench normalizes common model id styles before matching public catalogs:

| Input | Normalized |
| --- | --- |
| `gpt-5-5` | `gpt-5.5` |
| `claude-opus-4-8` | `claude-opus-4.8` |
| `xai/grok-4.3` | also tries `x-ai/grok-4.3` |
| `gemini/gemini-3.1-pro-preview` | also tries `google/...` and `vertex_ai/...` aliases |

If a model id contains a vendor prefix, EgoBench also tries the bare model id after the first slash. This keeps OpenRouter-style ids such as `anthropic/claude-opus-4.8` usable with direct-provider catalogs.

## Runtime Cost Ledger

The `phase_cost_log` table stores:

- `phase`
- `model`
- input token count
- output token count
- estimated `cost_usd`

The ledger uses the same resolver as the dry-run estimate for the command. This keeps estimate and runtime logging consistent, but it still means the ledger is a best estimate; EgoBench does not currently replace ledger values with provider invoice data.

Run:

```bash
uv run egobench cost
```

to summarize the ledger by phase and model.

## Limitations

- Provider billing is authoritative. Public catalogs can lag provider pages.
- Prompt caching, batch discounts, data residency premiums, regional cloud pricing, tool-use fees, image/audio/video tokens, and reasoning-token billing may not be fully represented unless the selected catalog entry includes them and EgoBench accounts for them.
- Providers declared without `api_key_env` or `api_key_keyring` are treated as local or unauthenticated endpoints and estimated at zero. If you are using a paid hosted endpoint, declare an API key env var or add explicit `[[pricing.models]]` overrides.
- OpenRouter routes can vary by provider. Catalog pricing is useful for dry runs, but exact OpenRouter request cost ultimately comes from generation metadata, which EgoBench does not currently write into `phase_cost_log`.
