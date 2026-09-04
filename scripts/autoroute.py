#!/usr/bin/env python3
"""Deterministic model/reasoning router for Codex tasks."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

try:
    from session_controller import current_thread_id, queue_model_switch, switch_command
except ImportError:  # pragma: no cover
    from scripts.session_controller import current_thread_id, queue_model_switch, switch_command

try:
    from router_policy import DIMENSIONS, EFFORTS, LEVELS, DEFAULT_WEIGHTS, BANDS, analyze, band_for, classify_workload, effort_floor, task_score
except ImportError:  # pragma: no cover
    from scripts.router_policy import DIMENSIONS, EFFORTS, LEVELS, DEFAULT_WEIGHTS, BANDS, analyze, band_for, classify_workload, effort_floor, task_score

MODEL_FAMILY_ORDER = {
    "low": ["luna", "terra", "gpt-5.2", "gpt-5.5", "sol"],
    "medium": ["terra", "gpt-5.2", "sol", "gpt-5.5", "luna"],
    "high": ["sol", "gpt-5.5", "gpt-5.2", "terra", "luna"],
    "xhigh": ["sol", "gpt-5.5", "gpt-5.2", "terra", "luna"],
    "max": ["sol", "gpt-5.5", "gpt-5.2", "terra", "luna"],
}
WORKLOADS = ["simple", "everyday", "debugging", "architecture", "research", "long_horizon", "high_risk"]
WORKLOAD_CHOICES = ["auto", *WORKLOADS]


def normalize_tier(value: Any) -> str | None:
    if isinstance(value, str) and value.lower() in LEVELS:
        return value.lower()
    if isinstance(value, int) and 0 <= value < len(LEVELS):
        return LEVELS[value]
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


def workload_model_families(workload: str) -> list[str]:
    return {
        "simple": ["luna", "terra", "gpt-5.2"],
        "everyday": ["terra", "gpt-5.2", "sol"],
        "debugging": ["sol", "gpt-5.5", "gpt-5.2"],
        "architecture": ["sol", "gpt-5.5", "gpt-5.2"],
        "research": ["gpt-5.5", "gpt-5.2", "sol"],
        "long_horizon": ["gpt-5.2", "sol", "gpt-5.5"],
        "high_risk": ["sol", "gpt-5.5", "gpt-5.2"],
    }[workload]


def model_family_rank(slug: str, level: str, long_horizon: bool = False) -> int:
    name = slug.lower()
    order = MODEL_FAMILY_ORDER.get(level, MODEL_FAMILY_ORDER["high"])
    if long_horizon and level in {"high", "xhigh", "max"}:
        order = ["gpt-5.2", "sol", "gpt-5.5", "terra", "luna"]
    for index, family in enumerate(order):
        if family == "sol" and "sol" in name:
            return index
        if family in name:
            return index
    return len(order)


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def load_config(path_arg: str | None) -> dict[str, Any]:
    config: dict[str, Any] = {"enabled": True, "mode": "auto"}
    candidates = []
    codex_home = Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser()
    default_config = codex_home / "autoroute.json"
    if default_config.exists():
        candidates.append(default_config)
    if path_arg:
        candidates.append(Path(path_arg).expanduser())
    if os.environ.get("AUTOROUTE_CONFIG"):
        candidates.append(Path(os.environ["AUTOROUTE_CONFIG"]).expanduser())
    for path in candidates:
        raw = read_json(path)
        if isinstance(raw, dict):
            section = raw.get("autoroute", raw)
            if isinstance(section, dict):
                config.update(section)
    if os.environ.get("AUTOROUTE_ENABLED") is not None:
        config["enabled"] = os.environ["AUTOROUTE_ENABLED"].lower() not in {"0", "false", "no", "off"}
    if os.environ.get("AUTOROUTE_MODE"):
        config["mode"] = os.environ["AUTOROUTE_MODE"].lower()
    config["mode"] = config.get("mode", "auto") if config.get("mode") in {"auto", "suggest", "manual"} else "auto"
    if config.get("workload") not in WORKLOAD_CHOICES:
        config["workload"] = "auto"
    return config


def current_codex_config() -> dict[str, str]:
    path = Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser() / "config.toml"
    values: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return values
    for key in ("model", "model_reasoning_effort"):
        match = re.search(rf"^\s*{re.escape(key)}\s*=\s*[\"']([^\"']+)[\"']", text, re.MULTILINE)
        if match:
            values[key] = match.group(1)
    return values


def state_path(config: dict[str, Any], explicit: str | None = None) -> Path:
    if explicit:
        return Path(explicit).expanduser()
    if config.get("state_file"):
        return Path(str(config["state_file"])).expanduser()
    cache_dir = config.get("cache_dir") or os.environ.get("AUTOROUTE_CACHE_DIR")
    if cache_dir:
        return Path(str(cache_dir)).expanduser() / "autoroute-state.json"
    if sys.platform == "darwin":
        default_dir = Path.home() / "Library" / "Caches" / "codex"
    else:
        default_dir = Path(os.environ.get("XDG_CACHE_HOME", "~/.cache")).expanduser() / "codex"
    return default_dir / "autoroute-state.json"


def fallback_state_path() -> Path:
    return Path(tempfile.gettempdir()) / "codex" / "autoroute-state.json"


def catalog_paths(config: dict[str, Any], explicit: str | None) -> list[Path]:
    # An explicitly supplied catalog is a complete routing inventory. Mixing it
    # with host-wide caches makes tests and custom-provider routing depend on
    # unrelated models installed on the machine.
    if explicit:
        return [Path(explicit).expanduser()]
    environment_file = os.environ.get("AUTOROUTE_MODELS_FILE")
    if environment_file:
        return [Path(environment_file).expanduser()]
    configured_file = config.get("models_file")
    if configured_file:
        return [Path(str(configured_file)).expanduser()]

    paths: list[Path] = []
    codex_home = Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser()
    paths.extend([codex_home / "models_cache.json", codex_home / "cc-switch-model-catalog.json"])
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        if path not in seen:
            seen.add(path)
            unique.append(path)
    return unique


def normalize_models(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, dict):
        raw = raw.get("models", raw.get("data", []))
    if not isinstance(raw, list):
        return []
    models = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        slug = item.get("slug") or item.get("id") or item.get("name")
        if not slug:
            continue
        levels = item.get("supported_reasoning_levels", item.get("reasoning_levels", []))
        if isinstance(levels, list):
            levels = [x.get("effort") if isinstance(x, dict) else str(x) for x in levels]
        elif isinstance(levels, str):
            levels = [levels]
        else:
            levels = []
        levels = [level for level in levels if level in EFFORTS]
        normalized_catalog_tier = normalize_tier(item.get("routing_tier") or item.get("capability_tier") or item.get("quality_tier"))
        inferred_tier = normalized_catalog_tier or infer_model_tier(str(slug))
        models.append({
            "slug": str(slug),
            "display_name": str(item.get("display_name") or slug),
            "reasoning": levels,
            "priority": int(item.get("priority", 10000)) if str(item.get("priority", "")).lstrip("-").isdigit() else 10000,
            "context_window": item.get("context_window"),
            "routing_tier": inferred_tier,
            "routing_source": "catalog" if normalized_catalog_tier else ("name" if inferred_tier else None),
        })
    return models


def discover_models(config: dict[str, Any], explicit: str | None) -> tuple[list[dict[str, Any]], str | None, bool]:
    merged: dict[str, dict[str, Any]] = {}
    sources = []
    # A file supplied through the CLI, config, or environment is authoritative;
    # do not silently add models from another inventory on the host.
    catalog_override = explicit or os.environ.get("AUTOROUTE_MODELS_FILE") or config.get("models_file")
    configured = [] if catalog_override else normalize_models(config.get("models", []))
    for model in configured:
        merged[model["slug"]] = model
    if configured:
        sources.append("autoroute config models")
    for path in catalog_paths(config, explicit):
        raw = read_json(path)
        models = normalize_models(raw)
        if models:
            sources.append(str(path))
            for model in models:
                if model["slug"] not in merged:
                    merged[model["slug"]] = model
    return list(merged.values()), ", ".join(sources) or None, not bool(merged)


def probe_effort(model: dict[str, Any], current: dict[str, str]) -> str:
    supported = [effort for effort in EFFORTS if effort in model.get("reasoning", [])]
    if model["slug"] == current.get("model") and current.get("model_reasoning_effort") in supported:
        return current["model_reasoning_effort"]
    for effort in ("low", "none", "medium", "high", "xhigh", "max", "ultra"):
        if effort in supported:
            return effort
    return current.get("model_reasoning_effort", "medium")


def probe_model(model: dict[str, Any], effort: str, timeout: int) -> dict[str, Any]:
    codex_bin = os.environ.get("AUTOROUTE_CODEX_BIN", "codex")
    command = [
        codex_bin, "exec", "--ephemeral", "--json", "--skip-git-repo-check",
        "-C", tempfile.gettempdir(), "-s", "read-only", "-m", model["slug"],
        "-c", f'model_reasoning_effort="{effort}"', "Reply with exactly: OK",
    ]
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=timeout,
        )
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        completed = subprocess.CompletedProcess(command, 124, exc.stdout or "", exc.stderr or "")
        timed_out = True
    except OSError as exc:
        return {
            "available": False,
            "probe_blocked": True,
            "tested_effort": effort,
            "latency_seconds": round(time.monotonic() - started, 3),
            "exit_code": getattr(exc, "errno", 1) or 1,
            "timed_out": False,
            "errors": [str(exc)],
            "stderr_tail": [],
        }
    completed_turn = False
    answer_ok = False
    errors = []
    stdout = completed.stdout.decode() if isinstance(completed.stdout, bytes) else completed.stdout
    stderr = completed.stderr.decode() if isinstance(completed.stderr, bytes) else completed.stderr
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "turn.completed":
            completed_turn = True
        if event.get("type") == "error":
            errors.append(event.get("message", ""))
        if event.get("type") == "item.completed":
            item = event.get("item", {})
            if item.get("type") == "agent_message" and item.get("text", "").strip() == "OK":
                answer_ok = True
            elif item.get("type") == "error":
                errors.append(item.get("message", ""))
    stderr_tail = [line[-500:] for line in stderr.splitlines()[-3:]]
    blocked_text = "\n".join([stderr, *errors]).lower()
    probe_blocked = any(marker in blocked_text for marker in (
        "operation not permitted",
        "permission denied",
        "failed to initialize in-process app-server",
        "sandbox",
    ))
    return {
        "available": completed.returncode == 0 and completed_turn and answer_ok and not timed_out,
        "probe_blocked": probe_blocked,
        "tested_effort": effort,
        "latency_seconds": round(time.monotonic() - started, 3),
        "exit_code": completed.returncode,
        "timed_out": timed_out,
        "errors": errors[-3:],
        "stderr_tail": stderr_tail,
    }


def refresh_model_state(models: list[dict[str, Any]], source: str | None, current: dict[str, str], path: Path, timeout: int) -> dict[str, Any]:
    results = {}
    workers = min(4, max(1, len(models)))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {}
        for model in models:
            effort = probe_effort(model, current)
            futures[executor.submit(probe_model, model, effort, timeout)] = model
        for future in as_completed(futures):
            model = futures[future]
            result = future.result()
            result["supported_efforts"] = model.get("reasoning", [])
            result["routing_tier"] = model.get("routing_tier")
            result["routing_source"] = model.get("routing_source")
            results[model["slug"]] = result
    blocked = [result.get("probe_blocked", False) for result in results.values()]
    state = {
        "version": 1,
        "refreshed_at": datetime.now(timezone.utc).isoformat(),
        "refreshed_epoch": time.time(),
        "source": source,
        "default_model": current.get("model"),
        "default_effort": current.get("model_reasoning_effort"),
        "models": results,
        "probe_status": "blocked" if blocked and all(blocked) else ("partial" if any(blocked) else "complete"),
    }
    candidates = [path]
    fallback = fallback_state_path()
    if fallback != path:
        candidates.append(fallback)
    write_error = None
    for target in candidates:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(state, indent=2), encoding="utf-8")
            state["cache_path"] = str(target)
            state["cache_write"] = {"path": str(target), "ok": True, "fallback": target != path}
            break
        except OSError as exc:
            write_error = str(exc)
    else:
        state["cache_path"] = str(path)
        state["cache_write"] = {"path": str(path), "ok": False, "error": write_error}
    return state


def load_model_state(path: Path, ttl_seconds: int) -> tuple[dict[str, Any] | None, bool]:
    raw = read_json(path)
    if not isinstance(raw, dict) or not isinstance(raw.get("models"), dict):
        return None, True
    refreshed = raw.get("refreshed_epoch", 0)
    try:
        stale = time.time() - float(refreshed) >= ttl_seconds
    except (TypeError, ValueError):
        stale = True
    return raw, stale


def cache_status(state: dict[str, Any] | None) -> dict[str, Any]:
    if not state:
        return {"ok": False, "status": "unavailable"}
    status = state.get("cache_write")
    if isinstance(status, dict):
        return status
    return {"ok": True, "path": "existing cache"}


def filter_verified_models(models: list[dict[str, Any]], state: dict[str, Any] | None, default_model: str | None) -> list[dict[str, Any]]:
    if not state:
        return models
    if state.get("probe_status") == "blocked":
        return models
    verified = []
    state_models = state.get("models", {})
    for model in models:
        probe = state_models.get(model["slug"], {})
        if probe.get("available") or model["slug"] == default_model:
            copy = dict(model)
            copy["verified_available"] = bool(probe.get("available"))
            verified.append(copy)
    return verified


def mark_model_unavailable(path: Path, model: str, message: str) -> None:
    state = read_json(path)
    if not isinstance(state, dict) or not isinstance(state.get("models"), dict):
        return
    entry = state["models"].setdefault(model, {})
    entry["available"] = False
    entry["runtime_failure"] = message
    entry["failed_at"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def number_signal(signals: dict[str, Any], key: str) -> int:
    value = signals.get(key, 0)
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def choose_model(models: list[dict[str, Any]], level: str, current: str | None, explicit: str | None, scores: dict[str, int] | None = None, workload: str = "everyday") -> tuple[dict[str, Any], str]:
    if explicit:
        for model in models:
            if model["slug"] == explicit:
                return model, "explicit model"
        return {"slug": explicit, "display_name": explicit, "reasoning": [], "priority": 0, "routing_tier": None}, "explicit model (not discovered)"
    target_tier = LEVELS.index(level) if level in LEVELS else 2
    scores = scores or {}
    if current and not any(model.get("routing_source") == "catalog" for model in models):
        for model in models:
            if model["slug"] == current:
                return model, "current configured model (catalog tier metadata unavailable)"
    preferred_families = workload_model_families(workload)
    for family in preferred_families:
        matches = [model for model in models if family == "sol" and "sol" in model["slug"].lower() or family in model["slug"].lower()]
        if matches:
            return sorted(matches, key=lambda model: (model_family_rank(model["slug"], level, workload == "long_horizon"), model["priority"]))[0], f"{workload} workload preference"
    long_horizon = workload == "long_horizon"
    tiered = []
    for model in models:
        tier = normalize_tier(model.get("routing_tier"))
        if tier:
            tiered.append((abs(LEVELS.index(tier) - target_tier), model_family_rank(model["slug"], level, long_horizon), model["priority"], model))
    if tiered:
        selected = sorted(tiered, key=lambda item: (item[0], item[1], item[2]))[0][3]
        source = selected.get("routing_source") or "inferred"
        return selected, f"{source} model tier + family preference"
    if current:
        for model in models:
            if model["slug"] == current:
                return model, "current configured model fallback"
    return sorted(models, key=lambda model: model["priority"])[0], "catalog priority fallback"


def clamp_effort(target: str, supported: list[str], current: str | None, explicit: str | None) -> tuple[str, str]:
    if explicit:
        if not supported or explicit in supported:
            return explicit, "explicit effort" if supported else "explicit effort (support undiscovered)"
        target = explicit
    supported = [level for level in EFFORTS if level in supported]
    if not supported:
        return current or target, "undiscovered effort support"
    target_index = EFFORTS.index(target)
    selected = min(supported, key=lambda level: abs(EFFORTS.index(level) - target_index))
    reason = "explicit effort unsupported; closest supported effort" if explicit else "closest supported effort"
    return selected, reason


def build_command(model: str, effort: str, prompt: str) -> list[str]:
    return [os.environ.get("AUTOROUTE_CODEX_BIN", "codex"), "-m", model, "-c", f'model_reasoning_effort="{effort}"', prompt]


def executable_available(executable: str) -> bool:
    if os.sep in executable:
        return os.path.isfile(executable) and os.access(executable, os.X_OK)
    return shutil.which(executable) is not None


def continuation_message(result: dict[str, Any]) -> str:
    """Make the queued turn explicit while relying on the thread's history."""
    return (
        "继续处理当前任务。AutoRoute 已根据当前任务建议切换到 "
        f"{result['model']} / {result['effort']}。保留并使用本会话已有的全部上下文、"
        "仓库状态和此前结论，从当前进度继续，不要重新开始。"
    )


# Keep the CLI's historical function surface while delegating catalog and cache
# responsibilities to focused modules.
try:
    from model_catalog import normalize_models as _catalog_normalize_models, discover_models as _catalog_discover_models
    from availability import load_model_state as _availability_load_model_state, cache_status as _availability_cache_status, filter_verified_models as _availability_filter_verified_models, fallback_state_path as _availability_fallback_state_path
except ImportError:  # pragma: no cover
    from scripts.model_catalog import normalize_models as _catalog_normalize_models, discover_models as _catalog_discover_models
    from scripts.availability import load_model_state as _availability_load_model_state, cache_status as _availability_cache_status, filter_verified_models as _availability_filter_verified_models, fallback_state_path as _availability_fallback_state_path

normalize_models = lambda raw: _catalog_normalize_models(raw, EFFORTS, LEVELS)
discover_models = lambda config, explicit: _catalog_discover_models(config, explicit, read_json, EFFORTS, LEVELS)
load_model_state = lambda path, ttl: _availability_load_model_state(path, ttl, read_json)
cache_status = _availability_cache_status
filter_verified_models = _availability_filter_verified_models
fallback_state_path = _availability_fallback_state_path


def route(args: argparse.Namespace) -> dict[str, Any]:
    config = load_config(args.config)
    mode = args.mode or config.get("mode", "auto")
    if not config.get("enabled", True):
        mode = "manual"
    signals = json.loads(args.signals) if args.signals else {}
    if not isinstance(signals, dict):
        raise ValueError("--signals must be a JSON object")
    score_overrides = json.loads(args.scores) if args.scores else {}
    if not isinstance(score_overrides, dict):
        raise ValueError("--scores must be a JSON object")
    current = current_codex_config()
    models, source, degraded = discover_models(config, args.models_file)
    cache_path = state_path(config, args.state_file)
    state, stale = load_model_state(cache_path, args.ttl)
    if os.environ.get("AUTOROUTE_SKIP_PROBE", "").lower() in {"1", "true", "yes"}:
        state, stale = None, True
    discovered_slugs = {model["slug"] for model in models}
    cached_slugs = set(state.get("models", {})) if state else set()
    inventory_changed = discovered_slugs != cached_slugs
    should_probe = os.environ.get("AUTOROUTE_SKIP_PROBE", "").lower() not in {"1", "true", "yes"}
    if should_probe and (args.refresh_models or state is None or stale or inventory_changed):
        if models:
            state = refresh_model_state(models, source, current, cache_path, args.probe_timeout)
            cache_path = Path(state.get("cache_path", cache_path))
            stale = False
            inventory_changed = False
    all_discovered_models = len(models)
    models = filter_verified_models(models, state, current.get("model"))
    if not models:
        fallback_model = args.model or current.get("model") or "current-configured-model"
        models = [{"slug": fallback_model, "display_name": fallback_model, "reasoning": [current.get("model_reasoning_effort", "high"), "none"], "priority": 0, "routing_tier": None}]
    rules = read_json(Path(args.rules).expanduser()) if args.rules else None
    weights = rules.get("weights", DEFAULT_WEIGHTS) if isinstance(rules, dict) else DEFAULT_WEIGHTS
    bands = None
    if isinstance(rules, dict) and isinstance(rules.get("bands"), list):
        parsed_bands = []
        for item in rules["bands"]:
            if isinstance(item, dict) and item.get("effort") in EFFORTS:
                parsed_bands.append((str(item.get("name", item["effort"])), int(item.get("min", 0)), int(item.get("max", 30)), item["effort"]))
        bands = parsed_bands or None
    scores, evidence = analyze(args.prompt, signals, score_overrides)
    score = task_score(scores, weights)
    level, target = band_for(score, bands)
    base_level = level
    adaptive = False
    if number_signal(signals, "test_failures") >= 2 or number_signal(signals, "retry_count") >= 3:
        target = EFFORTS[min(len(EFFORTS) - 1, EFFORTS.index(target) + 1)]
        if level in LEVELS:
            level = LEVELS[min(len(LEVELS) - 1, LEVELS.index(level) + 1)]
        adaptive = True
        evidence["iteration"].append("adaptive escalation threshold reached")
    requested_workload = args.workload or config.get("workload", "auto")
    workload = requested_workload if requested_workload != "auto" else classify_workload(args.prompt, scores)
    workload_source = "cli" if args.workload and args.workload != "auto" else (
        "config" if config.get("workload") and config.get("workload") != "auto" and not args.workload else "inferred"
    )
    if adaptive and workload in {"simple", "everyday"}:
        workload = "debugging"
        workload_source = "adaptive"
    target = effort_floor(target, workload)
    model_constraint = args.model or (current.get("model") if mode == "manual" else None)
    effort_constraint = args.effort or (current.get("model_reasoning_effort") if mode == "manual" else None)
    model, model_reason = choose_model(models, level, current.get("model"), model_constraint, scores, workload)
    effort, effort_reason = clamp_effort(target, model.get("reasoning", []), current.get("model_reasoning_effort"), effort_constraint)
    result = {
        "task": args.prompt,
        "mode": mode,
        "enabled": bool(config.get("enabled", True)),
        "model": model["slug"],
        "effort": effort,
        "level": level,
        "base_level": base_level,
        "score": score,
        "workload": workload,
        "workload_source": workload_source,
        "dimensions": {key: {"score": scores[key], "evidence": evidence[key]} for key in DIMENSIONS},
        "discovery": {
            "source": source,
            "degraded": degraded,
            "model_count": all_discovered_models,
            "verified_count": len(models),
        },
        "availability": {
            "state_file": str(cache_path),
            "refreshed_at": state.get("refreshed_at") if state else None,
            "stale": stale,
            "inventory_changed": inventory_changed,
            "discovered_count": all_discovered_models,
            "verified_count": sum(1 for model in models if model.get("verified_available")),
            "default_model_fallback": current.get("model"),
            "cache": cache_status(state),
            "probe_status": state.get("probe_status") if state else "not_run",
            "models": state.get("models", {}) if state else {},
        },
        "selection": {"model_reason": model_reason, "effort_reason": effort_reason},
        "model_tier": model.get("routing_tier"),
        "model_tier_source": model.get("routing_source"),
        "adaptive_escalation": adaptive,
        "command": build_command(model["slug"], effort, args.prompt),
        "changed_current_session": False,
        "session": {
            "thread_id": current_thread_id(),
            "switch_available": bool(current_thread_id()),
            "switch_command": None,
        },
    }
    if mode == "manual":
        result["note"] = "Manual mode: current Codex settings are untouched."
    elif mode == "suggest":
        result["note"] = "Suggest mode: recommendation only until the user chooses whether to switch or continue."
    else:
        result["note"] = (
            "Auto mode applies the model and reasoning effort when launching or explicitly "
            "queueing work; the existing thread can be continued without losing its history."
        )
    result["session"]["switch_command"] = queue_switch_preview(result)
    return result


def queue_switch_preview(result: dict[str, Any]) -> list[str] | None:
    """Expose the exact current-thread action without executing it."""
    return switch_command(result["model"], result["effort"], continuation_message(result))


def main() -> int:
    parser = argparse.ArgumentParser(description="Route a coding task to a discovered Codex model and reasoning effort.")
    parser.add_argument("prompt", nargs="?", default="refresh model inventory", help="Task to analyze")
    parser.add_argument("--mode", choices=["auto", "suggest", "manual"])
    parser.add_argument("--config")
    parser.add_argument("--models-file")
    parser.add_argument("--rules")
    parser.add_argument("--signals", help="JSON object of observed runtime signals")
    parser.add_argument("--scores", help="JSON object overriding semantic dimension scores (0-5)")
    parser.add_argument("--workload", choices=WORKLOAD_CHOICES, help="Workload specialization; auto infers it from the task")
    parser.add_argument("--model", help="Explicit model constraint")
    parser.add_argument("--effort", choices=EFFORTS, help="Explicit reasoning effort constraint")
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--run", action="store_true", help="Start a separate Codex process with the recommendation")
    parser.add_argument("--session", action="store_true", help="Queue a model switch and continuation on the current Codex thread")
    parser.add_argument("--refresh-models", action="store_true", help="Probe every discovered model now")
    parser.add_argument("--list-models", action="store_true", help="List discovered and verified models, then exit")
    parser.add_argument("--state-file", help="Availability state JSON path")
    parser.add_argument("--ttl", type=int, default=900, help="Availability cache TTL in seconds")
    parser.add_argument("--probe-timeout", type=int, default=45, help="Per-model availability probe timeout")
    args = parser.parse_args()
    if args.run and args.session:
        parser.error("--run and --session are mutually exclusive")
    if not args.as_json and not args.list_models:
        print(f"[AutoRoute] 分析任务中...", file=sys.stderr)
    try:
        result = route(args)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    if args.session:
        ok, command, error = queue_model_switch(result["model"], result["effort"], continuation_message(result))
        result["session"]["switch_command"] = command or result["session"].get("switch_command")
        result["session"]["switch_queued"] = ok
        result["session"]["error"] = error
        result["changed_current_session"] = ok
        if not ok:
            print(f"[AutoRoute] 无法切换当前会话: {error}", file=sys.stderr)
            return 2
    if args.list_models:
        print(json.dumps({"discovery": result["discovery"], "availability": result["availability"], "selected": {"model": result["model"], "effort": result["effort"]}}, indent=2))
        return 0
    if args.as_json:
        print(json.dumps(result, indent=2, ensure_ascii=True))
    else:
        print(f"🎯 推荐模型: {result['model']}  |  推理强度: {result['effort']}")
        print(f"   任务等级: {result['level']} (score {result['score']}/30, {result['workload']})")
        print()
        print(result["note"])
        for key, value in result["dimensions"].items():
            reasons = "; ".join(value["evidence"]) or "no strong signal"
            print(f"- {key}: {value['score']}/5 ({reasons})")
        print(f"Discovery: {result['discovery']['source'] or 'fallback'}")
        print("Command: " + shlex.join(result["command"]))
        if result["session"].get("switch_available"):
            print()
            print("选择下一步：")
            print(f"1. 切换到 {result['model']} / {result['effort']} 并继续（保留当前 thread 上下文）")
            print("2. 保持当前模型继续")
            print("确认选择 1 后执行：python3 scripts/autoroute.py --session " + shlex.quote(args.prompt))
    if args.run:
        if result["mode"] != "auto":
            print("Refusing --run unless mode is auto.", file=sys.stderr)
            return 2
        executable = result["command"][0]
        if not executable_available(executable):
            print(f"{executable} executable not found; command was printed but not run.", file=sys.stderr)
            return 2
        completed = subprocess.run(result["command"])
        if completed.returncode != 0:
            mark_model_unavailable(Path(result["availability"]["state_file"]), result["model"], f"runtime exit code {completed.returncode}")
            default_model = current_codex_config().get("model")
            default_effort = current_codex_config().get("model_reasoning_effort", "high")
            if default_model and default_model != result["model"]:
                default_entry = result["availability"].get("models", {}).get(default_model, {})
                supported = default_entry.get("supported_efforts", []) if isinstance(default_entry, dict) else []
                default_effort, _ = clamp_effort(default_effort, supported, default_effort, default_effort if supported else None)
                fallback_command = build_command(default_model, default_effort, args.prompt)
                print(f"Selected model failed; retrying with default model {default_model}.", file=sys.stderr)
                return subprocess.run(fallback_command).returncode
        return completed.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
