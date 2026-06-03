from __future__ import annotations

import json
import re
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable


OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
LITELLM_PRICES_URL = "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
DEFAULT_CACHE_TTL_SECONDS = 24 * 60 * 60
FETCH_TIMEOUT_SECONDS = 8


@dataclass(frozen=True)
class ModelPrice:
    input_per_1k: float
    output_per_1k: float


@dataclass(frozen=True)
class PriceOverride:
    model: str
    price: ModelPrice
    provider: str | None = None


@dataclass(frozen=True)
class PriceQuote:
    price: ModelPrice
    confidence: str
    source: str
    matched_model: str | None = None

    @property
    def known(self) -> bool:
        return self.confidence != "unknown"

    @property
    def approximate(self) -> bool:
        return self.confidence in {"normalized", "rough"}

    @property
    def price_label(self) -> str:
        if not self.known:
            return "unknown"
        if self.source == "local":
            return "local"
        if self.confidence == "exact":
            return self.source
        return f"{self.confidence}/{self.source}"

    def cost_usd(self, input_tokens: int, output_tokens: int) -> float:
        if not self.known:
            return 0.0
        return (input_tokens / 1000.0 * self.price.input_per_1k) + (
            output_tokens / 1000.0 * self.price.output_per_1k
        )

    def cost_label(self, input_tokens: int, output_tokens: int) -> str:
        if not self.known:
            return "unknown"
        prefix = "≈" if self.approximate else ""
        return f"{prefix}${self.cost_usd(input_tokens, output_tokens):.2f}"


@dataclass(frozen=True)
class _Candidate:
    key: str
    confidence: str


@dataclass(frozen=True)
class _CatalogQuote:
    price: ModelPrice
    source: str
    matched_model: str


# Built-ins are only the offline fallback. The live/cache sources below are
# preferred for normal CLI use because model pricing changes frequently.
PRICES: dict[str, ModelPrice] = {
    "claude-opus-4-8": ModelPrice(input_per_1k=0.005, output_per_1k=0.025),
    "claude-opus-4-7": ModelPrice(input_per_1k=0.005, output_per_1k=0.025),
    "claude-sonnet-4": ModelPrice(input_per_1k=0.003, output_per_1k=0.015),
    "claude-haiku-4-5-20251001": ModelPrice(input_per_1k=0.001, output_per_1k=0.005),
    "gpt-5.5": ModelPrice(input_per_1k=0.005, output_per_1k=0.030),
    "gpt-5.4": ModelPrice(input_per_1k=0.0025, output_per_1k=0.015),
    "gpt-5.4-mini": ModelPrice(input_per_1k=0.00075, output_per_1k=0.0045),
    "gpt-5": ModelPrice(input_per_1k=0.005, output_per_1k=0.015),
    "gpt-4.1": ModelPrice(input_per_1k=0.002, output_per_1k=0.008),
    "gemini-3.1-pro-preview": ModelPrice(input_per_1k=0.002, output_per_1k=0.012),
    "grok-4.3": ModelPrice(input_per_1k=0.00125, output_per_1k=0.0025),
    "text-embedding-3-small": ModelPrice(input_per_1k=0.00002, output_per_1k=0.0),
}


class PricingResolver:
    def __init__(
        self,
        *,
        overrides: Iterable[PriceOverride] = (),
        cache_dir: Path | None = None,
        fetch_external: bool = False,
        cache_ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
        fetcher: Callable[[str], dict[str, Any]] | None = None,
        openrouter_catalog: dict[str, Any] | None = None,
        litellm_catalog: dict[str, Any] | None = None,
    ):
        self.overrides = list(overrides)
        self.cache_dir = cache_dir
        self.fetch_external = fetch_external
        self.cache_ttl_seconds = cache_ttl_seconds
        self.fetcher = fetcher or _fetch_json
        self._openrouter_catalog = openrouter_catalog
        self._litellm_catalog = litellm_catalog
        self._override_index = _index_overrides(self.overrides)
        self._openrouter_index: dict[str, _CatalogQuote] | None = None
        self._litellm_index: dict[str, _CatalogQuote] | None = None

    @classmethod
    def from_config(
        cls,
        cfg: Any,
        *,
        cache_dir: Path | None = None,
        fetch_external: bool | None = None,
    ) -> PricingResolver:
        pricing_cfg = getattr(cfg, "pricing", None)
        if fetch_external is None:
            fetch_external = getattr(pricing_cfg, "fetch_external", True)
        overrides = [
            PriceOverride(
                provider=getattr(item, "provider", None),
                model=str(getattr(item, "model")),
                price=ModelPrice(
                    input_per_1k=float(getattr(item, "input_per_1m")) / 1000.0,
                    output_per_1k=float(getattr(item, "output_per_1m")) / 1000.0,
                ),
            )
            for item in getattr(pricing_cfg, "models", [])
        ]
        return cls(overrides=overrides, cache_dir=cache_dir, fetch_external=fetch_external)

    def quote_for(
        self,
        model: str,
        *,
        provider: str | None = None,
        local: bool = False,
    ) -> PriceQuote:
        if local:
            return PriceQuote(ModelPrice(0.0, 0.0), "exact", "local", matched_model=model)

        candidates = _candidate_keys(provider, model)
        exact_candidates = [candidate for candidate in candidates if candidate.confidence == "exact"]
        normalized_candidates = [candidate for candidate in candidates if candidate.confidence != "exact"]

        source_order = ("openrouter", "litellm", "builtin") if provider == "openrouter" else (
            "litellm",
            "openrouter",
            "builtin",
        )
        for candidate_group in (exact_candidates, normalized_candidates):
            override_quote = self._lookup_source("config", candidate_group)
            if override_quote is not None:
                return override_quote
            for source in source_order:
                quote = self._lookup_source(source, candidate_group)
                if quote is not None:
                    return quote

        rough = _rough_family_quote(provider, model)
        if rough is not None:
            return rough
        return PriceQuote(ModelPrice(0.0, 0.0), "unknown", "unknown", matched_model=None)

    def _lookup_source(self, source: str, candidates: list[_Candidate]) -> PriceQuote | None:
        for candidate in candidates:
            catalog_quote = self._lookup_one(source, candidate.key)
            if catalog_quote is not None:
                return PriceQuote(
                    catalog_quote.price,
                    candidate.confidence,
                    catalog_quote.source,
                    matched_model=catalog_quote.matched_model,
                )
        return None

    def _lookup_one(self, source: str, key: str) -> _CatalogQuote | None:
        normalized_key = key.lower()
        if source == "config":
            return self._override_index.get(normalized_key)
        if source == "builtin":
            price = PRICES.get(normalized_key)
            if price is not None:
                return _CatalogQuote(price, "builtin", normalized_key)
            return None
        if source == "openrouter":
            return self._openrouter_index_for_lookup().get(normalized_key)
        if source == "litellm":
            return self._litellm_index_for_lookup().get(normalized_key)
        return None

    def _openrouter_index_for_lookup(self) -> dict[str, _CatalogQuote]:
        if self._openrouter_index is None:
            catalog = self._openrouter_catalog
            if catalog is None:
                catalog = self._load_catalog("openrouter_models.json", OPENROUTER_MODELS_URL)
            self._openrouter_index = _index_openrouter(catalog or {})
        return self._openrouter_index

    def _litellm_index_for_lookup(self) -> dict[str, _CatalogQuote]:
        if self._litellm_index is None:
            catalog = self._litellm_catalog
            if catalog is None:
                catalog = self._load_catalog("litellm_model_prices.json", LITELLM_PRICES_URL)
            self._litellm_index = _index_litellm(catalog or {})
        return self._litellm_index

    def _load_catalog(self, filename: str, url: str) -> dict[str, Any] | None:
        cache_path = self.cache_dir / filename if self.cache_dir is not None else None
        if cache_path is not None and cache_path.exists() and not self._cache_stale(cache_path):
            return _read_json(cache_path)
        if self.fetch_external:
            try:
                payload = self.fetcher(url)
            except Exception:
                payload = None
            if payload is not None:
                if cache_path is not None:
                    cache_path.parent.mkdir(parents=True, exist_ok=True)
                    cache_path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
                return payload
        if cache_path is not None and cache_path.exists():
            return _read_json(cache_path)
        return None

    def _cache_stale(self, path: Path) -> bool:
        try:
            age = time.time() - path.stat().st_mtime
        except OSError:
            return True
        return age > self.cache_ttl_seconds


def has_price(model: str) -> bool:
    return _DEFAULT_RESOLVER.quote_for(model).known


def price_for(model: str) -> ModelPrice:
    return _DEFAULT_RESOLVER.quote_for(model).price


def quote_for_model(
    model: str,
    *,
    provider: str | None = None,
    resolver: PricingResolver | None = None,
    local: bool = False,
) -> PriceQuote:
    return (resolver or _DEFAULT_RESOLVER).quote_for(model, provider=provider, local=local)


def estimate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    *,
    provider: str | None = None,
    resolver: PricingResolver | None = None,
    local: bool = False,
) -> float:
    return quote_for_model(model, provider=provider, resolver=resolver, local=local).cost_usd(
        input_tokens,
        output_tokens,
    )


def _index_openrouter(catalog: dict[str, Any]) -> dict[str, _CatalogQuote]:
    out: dict[str, _CatalogQuote] = {}
    for item in catalog.get("data", []):
        if not isinstance(item, dict):
            continue
        pricing = item.get("pricing", {})
        if not isinstance(pricing, dict):
            continue
        prompt = _float_or_none(pricing.get("prompt"))
        completion = _float_or_none(pricing.get("completion"))
        if prompt is None or completion is None:
            continue
        quote = _CatalogQuote(
            ModelPrice(input_per_1k=prompt * 1000.0, output_per_1k=completion * 1000.0),
            "openrouter",
            str(item.get("id") or item.get("canonical_slug") or ""),
        )
        for key in (item.get("id"), item.get("canonical_slug")):
            if key:
                out[str(key).lower()] = quote
    return out


def _index_litellm(catalog: dict[str, Any]) -> dict[str, _CatalogQuote]:
    out: dict[str, _CatalogQuote] = {}
    for key, item in catalog.items():
        if key == "sample_spec" or not isinstance(item, dict):
            continue
        input_cost = _float_or_none(item.get("input_cost_per_token"))
        output_cost = _float_or_none(item.get("output_cost_per_token"))
        if input_cost is None or output_cost is None:
            continue
        out[str(key).lower()] = _CatalogQuote(
            ModelPrice(input_per_1k=input_cost * 1000.0, output_per_1k=output_cost * 1000.0),
            "litellm",
            str(key),
        )
    return out


def _index_overrides(overrides: list[PriceOverride]) -> dict[str, _CatalogQuote]:
    out: dict[str, _CatalogQuote] = {}
    for override in overrides:
        model = override.model.strip().lower()
        quote = _CatalogQuote(override.price, "config", override.model)
        if override.provider:
            # Provider-scoped overrides must only match that provider, so index
            # them under provider-prefixed keys and never under the bare model id.
            keys = []
            for alias in _provider_aliases(override.provider):
                keys.append(f"{alias}/{model}")
                keys.append(f"{alias}/{_normalize_model_id(model)}")
        else:
            keys = [model, _normalize_model_id(model)]
        for key in keys:
            out[key.lower()] = quote
    return out


def _candidate_keys(provider: str | None, model: str) -> list[_Candidate]:
    keys: list[_Candidate] = []
    raw_model = model.strip()
    bare_model = raw_model.partition("/")[2] if "/" in raw_model else raw_model
    normalized_model = _normalize_model_id(bare_model)
    normalized_raw = _normalize_model_id(raw_model)

    def add(key: str, confidence: str) -> None:
        key = key.strip()
        if not key:
            return
        candidate = _Candidate(key.lower(), confidence)
        if candidate not in keys:
            keys.append(candidate)

    add(raw_model, "exact")
    if raw_model != bare_model:
        add(bare_model, "exact")
    if provider:
        for alias in _provider_aliases(provider):
            add(f"{alias}/{raw_model}", "exact")
            add(f"{alias}/{bare_model}", "exact")

    if normalized_raw != raw_model:
        add(normalized_raw, "normalized")
    if normalized_model != bare_model:
        add(normalized_model, "normalized")
    if provider:
        for alias in _provider_aliases(provider):
            if normalized_raw != raw_model:
                add(f"{alias}/{normalized_raw}", "normalized")
            if normalized_model != bare_model:
                add(f"{alias}/{normalized_model}", "normalized")
    return keys


def _provider_aliases(provider: str) -> tuple[str, ...]:
    normalized = provider.strip().lower()
    aliases = {
        "xai": ("xai", "x-ai"),
        "x-ai": ("x-ai", "xai"),
        "google": ("google", "gemini", "vertex_ai"),
        "gemini": ("gemini", "google", "vertex_ai"),
        "vertex_ai": ("vertex_ai", "google", "gemini"),
    }
    return aliases.get(normalized, (normalized,))


def _normalize_model_id(model: str) -> str:
    normalized = model.strip().lower()
    normalized = re.sub(r"\bgpt-(\d+)-(\d+)\b", r"gpt-\1.\2", normalized)
    normalized = re.sub(
        r"\bclaude-(opus|sonnet|haiku)-(\d+)-(\d+)\b",
        r"claude-\1-\2.\3",
        normalized,
    )
    return normalized


def _rough_family_quote(provider: str | None, model: str) -> PriceQuote | None:
    bare = model.partition("/")[2] if "/" in model else model
    normalized = _normalize_model_id(bare)
    provider_norm = (provider or "").lower()
    source = "family"

    if provider_norm == "anthropic" or normalized.startswith("claude-"):
        if re.search(r"claude-opus-4\.[5-9]", normalized):
            return PriceQuote(ModelPrice(0.005, 0.025), "rough", source, "claude-opus-4.x")
        if normalized.startswith("claude-sonnet-4"):
            return PriceQuote(ModelPrice(0.003, 0.015), "rough", source, "claude-sonnet-4.x")
        if normalized.startswith("claude-haiku-4"):
            return PriceQuote(ModelPrice(0.001, 0.005), "rough", source, "claude-haiku-4.x")

    if provider_norm == "openai" or normalized.startswith("gpt-"):
        if normalized.startswith("gpt-5.5"):
            return PriceQuote(ModelPrice(0.005, 0.030), "rough", source, "gpt-5.5")
        if normalized.startswith("gpt-5.4-mini"):
            return PriceQuote(ModelPrice(0.00075, 0.0045), "rough", source, "gpt-5.4-mini")
        if normalized.startswith("gpt-5.4"):
            return PriceQuote(ModelPrice(0.0025, 0.015), "rough", source, "gpt-5.4")

    if provider_norm in {"google", "gemini", "vertex_ai"} or normalized.startswith("gemini-"):
        if normalized.startswith("gemini-3.1-pro"):
            return PriceQuote(ModelPrice(0.002, 0.012), "rough", source, "gemini-3.1-pro")
        if normalized.startswith("gemini-3.5-flash"):
            return PriceQuote(ModelPrice(0.0015, 0.009), "rough", source, "gemini-3.5-flash")

    if provider_norm in {"xai", "x-ai"} or normalized.startswith("grok-"):
        if normalized.startswith("grok-4"):
            return PriceQuote(ModelPrice(0.00125, 0.0025), "rough", source, "grok-4.x")
    return None


def _fetch_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": "egobench-pricing/0.1"})
    with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT_SECONDS) as response:
        return json.loads(response.read().decode("utf-8"))


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


_DEFAULT_RESOLVER = PricingResolver(fetch_external=False)
