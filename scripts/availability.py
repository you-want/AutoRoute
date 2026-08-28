"""Availability probing and persistent state management."""
from __future__ import annotations

import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


def load_model_state(path: Path, ttl_seconds: int, read_json: Callable[[Path], Any]) -> tuple[dict[str, Any] | None, bool]:
    raw = read_json(path)
    if not isinstance(raw, dict) or not isinstance(raw.get("models"), dict):
        return None, True
    try:
        stale = time.time() - float(raw.get("refreshed_epoch", 0)) >= ttl_seconds
    except (TypeError, ValueError):
        stale = True
    return raw, stale


def cache_status(state: dict[str, Any] | None) -> dict[str, Any]:
    if not state:
        return {"ok": False, "status": "unavailable"}
    return state.get("cache_write") if isinstance(state.get("cache_write"), dict) else {"ok": True, "path": "existing cache"}


def fallback_state_path() -> Path:
    return Path(tempfile.gettempdir()) / "codex" / "autoroute-state.json"


def filter_verified_models(models: list[dict[str, Any]], state: dict[str, Any] | None, default_model: str | None) -> list[dict[str, Any]]:
    if not state or state.get("probe_status") == "blocked":
        return models
    verified = []
    for model in models:
        probe = state.get("models", {}).get(model["slug"], {})
        if probe.get("available") or model["slug"] == default_model:
            copy = dict(model)
            copy["verified_available"] = bool(probe.get("available"))
            verified.append(copy)
    return verified
