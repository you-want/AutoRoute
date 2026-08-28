"""Model catalog discovery and normalization."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable


def normalize_tier(value: Any, levels: list[str]) -> str | None:
    if isinstance(value, str) and value.lower() in levels:
        return value.lower()
    if isinstance(value, int) and 0 <= value < len(levels):
        return levels[value]
    return None


def infer_model_tier(slug: str) -> str | None:
    name = slug.lower()
    if "luna" in name:
        return "low"
    if "terra" in name or name in {"gpt-5.2", "gpt-5.2-codex"}:
        return "medium"
    if "sol" in name or name in {"gpt-5.5", "gpt-5.5-codex"}:
        return "high"
    return None


def normalize_models(raw: Any, efforts: list[str], levels: list[str]) -> list[dict[str, Any]]:
    if isinstance(raw, dict):
        raw = raw.get("models", raw.get("data", []))
    if not isinstance(raw, list):
        return []
    models = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        slug = item.get("slug") or item.get("id") or item.get("name")
        if not slug or str(slug) in seen:
            continue
        seen.add(str(slug))
        levels_raw = item.get("supported_reasoning_levels", item.get("reasoning_levels", []))
        if isinstance(levels_raw, list):
            levels_raw = [x.get("effort") if isinstance(x, dict) else str(x) for x in levels_raw]
        elif isinstance(levels_raw, str):
            levels_raw = [levels_raw]
        else:
            levels_raw = []
        reasoning = [level for level in levels_raw if level in efforts]
        catalog_tier = normalize_tier(item.get("routing_tier") or item.get("capability_tier") or item.get("quality_tier"), levels)
        inferred_tier = catalog_tier or infer_model_tier(str(slug))
        models.append({"slug": str(slug), "display_name": str(item.get("display_name") or slug),
                       "reasoning": reasoning, "priority": int(item.get("priority", 10000)) if str(item.get("priority", "")).lstrip("-").isdigit() else 10000,
                       "context_window": item.get("context_window"), "routing_tier": inferred_tier,
                       "routing_source": "catalog" if catalog_tier else ("name" if inferred_tier else None)})
    return models


def catalog_paths(config: dict[str, Any], explicit: str | None) -> list[Path]:
    if explicit:
        return [Path(explicit).expanduser()]
    if os.environ.get("AUTOROUTE_MODELS_FILE"):
        return [Path(os.environ["AUTOROUTE_MODELS_FILE"]).expanduser()]
    if config.get("models_file"):
        return [Path(str(config["models_file"])).expanduser()]
    home = Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser()
    return [home / "models_cache.json", home / "cc-switch-model-catalog.json"]


def discover_models(config: dict[str, Any], explicit: str | None, read_json: Callable[[Path], Any], efforts: list[str], levels: list[str]) -> tuple[list[dict[str, Any]], str | None, bool]:
    merged: dict[str, dict[str, Any]] = {}
    override = explicit or os.environ.get("AUTOROUTE_MODELS_FILE") or config.get("models_file")
    configured = [] if override else normalize_models(config.get("models", []), efforts, levels)
    for model in configured:
        merged[model["slug"]] = model
    sources = ["autoroute config models"] if configured else []
    for path in catalog_paths(config, explicit):
        models = normalize_models(read_json(path), efforts, levels)
        if models:
            sources.append(str(path))
            for model in models:
                merged.setdefault(model["slug"], model)
    return list(merged.values()), ", ".join(sources) or None, not bool(merged)
