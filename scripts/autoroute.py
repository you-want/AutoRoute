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


EFFORTS = ["none", "low", "medium", "high", "xhigh", "max", "ultra"]
DEFAULT_WEIGHTS = {
    "complexity": 1.2,
    "scope": 1.0,
    "reasoning": 1.2,
    "risk": 1.2,
    "context": 0.8,
    "iteration": 1.0,
}
BANDS = [
    ("low", 0, 6, "low"),
    ("medium", 7, 12, "medium"),
    ("high", 13, 18, "high"),
    ("xhigh", 19, 24, "xhigh"),
    ("max", 25, 30, "max"),
]
DIMENSIONS = tuple(DEFAULT_WEIGHTS)
LEVELS = ["low", "medium", "high", "xhigh", "max"]
MODEL_FAMILY_ORDER = {
    "low": ["luna", "terra", "gpt-5.2", "gpt-5.5", "sol"],
    "medium": ["terra", "gpt-5.2", "sol", "gpt-5.5", "luna"],
    "high": ["sol", "gpt-5.5", "gpt-5.2", "terra", "luna"],
    "xhigh": ["sol", "gpt-5.5", "gpt-5.2", "terra", "luna"],
    "max": ["sol", "gpt-5.5", "gpt-5.2", "terra", "luna"],
}
WORKLOADS = ["simple", "everyday", "debugging", "architecture", "research", "long_horizon", "high_risk"]


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


def classify_workload(prompt: str, scores: dict[str, int]) -> str:
    text = prompt.lower()
    if scores.get("risk", 0) >= 4 or re.search(r"security|production|migration|rollback|安全|生产|迁移", text):
        return "high_risk"
    if scores.get("iteration", 0) >= 4 or scores.get("context", 0) >= 4 or re.search(r"long[- ]?term|long[- ]?horizon|roadmap|full implementation plan|长期|完整方案|路线图", text):
        return "long_horizon"
    if re.search(r"research|compare|comparison|evaluate|investigate|survey|benchmark|研究|调研|对比|评估", text):
        return "research"
    if re.search(r"debug|bug|race|flaky|root cause|调试|故障|根因|偶发", text):
        return "debugging"
    if scores.get("complexity", 0) >= 4 or re.search(r"architecture|distributed|concurr|system design|架构|分布式|并发|系统设计", text):
        return "architecture"
    if re.search(r"implement|build|create|add feature|新增功能|实现|开发|增加功能", text):
        return "everyday"
    if scores.get("complexity", 0) <= 1 and scores.get("scope", 0) <= 1 and scores.get("reasoning", 0) <= 1:
        return "simple"
    return "everyday"


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
    codex_home = Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser()
    return codex_home / "autoroute-state.json"


def catalog_paths(config: dict[str, Any], explicit: str | None) -> list[Path]:
    paths: list[Path] = []
    for value in (explicit, config.get("models_file"), os.environ.get("AUTOROUTE_MODELS_FILE")):
        if value:
            paths.append(Path(str(value)).expanduser())
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
    configured = normalize_models(config.get("models", []))
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
    return {
        "available": completed.returncode == 0 and completed_turn and answer_ok and not timed_out,
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
    state = {
        "version": 1,
        "refreshed_at": datetime.now(timezone.utc).isoformat(),
        "refreshed_epoch": time.time(),
        "source": source,
        "default_model": current.get("model"),
        "default_effort": current.get("model_reasoning_effort"),
        "models": results,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")
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


def filter_verified_models(models: list[dict[str, Any]], state: dict[str, Any] | None, default_model: str | None) -> list[dict[str, Any]]:
    if not state:
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


def analyze(prompt: str, signals: dict[str, Any], score_overrides: dict[str, Any] | None = None) -> tuple[dict[str, int], dict[str, list[str]]]:
    text = prompt.lower()
    scores = {dimension: 0 for dimension in DIMENSIONS}
    evidence: dict[str, list[str]] = {dimension: [] for dimension in DIMENSIONS}

    def add(dimension: str, amount: int, reason: str) -> None:
        scores[dimension] = min(5, scores[dimension] + amount)
        if reason not in evidence[dimension]:
            evidence[dimension].append(reason)

    if prompt.strip():
        add("complexity", 1, "concrete implementation request")
        add("reasoning", 1, "implementation reasoning")
        add("scope", 1, "default single-task scope")

    if re.search(r"architecture|design|trade-?off|distributed|concurr|协同|架构|设计", text):
        add("complexity", 3, "architecture or distributed design")
        add("reasoning", 3, "tradeoffs/design reasoning")
        add("iteration", 1, "design usually needs staged validation")
    if re.search(r"algorithm|performance|optimi[sz]|parser|migration|算法|性能|迁移", text):
        add("complexity", 2, "algorithmic, performance, or migration work")
        add("reasoning", 2, "non-trivial technical reasoning")
    if re.search(r"debug|bug|race|intermittent|flaky|root cause|修复|调试|偶发", text):
        add("complexity", 2, "debugging or uncertain root cause")
        add("reasoning", 3, "diagnosis and root-cause analysis")
        add("iteration", 2, "likely inspect-test-retry loop")
    if re.search(r"across the page|across modules|multiple components|跨模块|全页面", text):
        add("scope", 2, "behavior spans multiple components")
        add("context", 1, "surrounding state context required")
    if re.search(r"plan|rollout|rollback|migration plan|方案|排期", text):
        add("iteration", 2, "staged plan or rollout")
    if re.search(r"repo|repository|codebase|all modules|whole project|cross[- ]?repo|仓库|全项目", text):
        add("scope", 4, "repository-wide or cross-module scope")
        add("context", 2, "broad codebase context")
    if re.search(r"multi[- ]?file|several files|multiple files|模块|多个文件", text):
        add("scope", 2, "multiple-file change")
    if re.search(r"security|auth|credential|secret|production|data loss|rollback|安全|生产|数据", text):
        add("risk", 4, "security, production, or data-impacting work")
    if re.search(r"test|benchmark|validate|rollout|compatib|测试|验证|兼容", text):
        add("iteration", 1, "explicit validation or compatibility work")
    if len(prompt) > 1200:
        add("context", 4, "large task description")
    elif len(prompt) > 500:
        add("context", 2, "substantial task description")
    for key, dimension, amount, reason in (
        ("test_failures", "iteration", 2, "reported test failures"),
        ("retry_count", "iteration", 1, "repeated retries"),
        ("changed_files", "scope", 2, "large observed diff"),
        ("cross_language", "scope", 2, "cross-language change"),
        ("production_risk", "risk", 2, "elevated runtime risk"),
    ):
        if number_signal(signals, key) > 0 or signals.get(key) is True:
            add(dimension, amount, reason)
    if score_overrides:
        for dimension in DIMENSIONS:
            if dimension not in score_overrides:
                continue
            try:
                override = int(score_overrides[dimension])
            except (TypeError, ValueError):
                raise ValueError(f"score override for {dimension} must be an integer from 0 to 5")
            if not 0 <= override <= 5:
                raise ValueError(f"score override for {dimension} must be from 0 to 5")
            scores[dimension] = override
            evidence[dimension] = ["explicit semantic score override"]
    return scores, evidence


def task_score(scores: dict[str, int], weights: dict[str, float] | None = None) -> int:
    weights = weights or DEFAULT_WEIGHTS
    raw = sum(scores[key] * float(weights.get(key, 1)) for key in DIMENSIONS)
    maximum = sum(5 * float(weights.get(key, 1)) for key in DIMENSIONS)
    return round(raw / maximum * 30) if maximum else 0


def band_for(score: int, bands: list[tuple[str, int, int, str]] | None = None) -> tuple[str, str]:
    bands = bands or BANDS
    for name, minimum, maximum, effort in bands:
        if minimum <= score <= maximum:
            return name, effort
    return bands[-1][0], bands[-1][3]


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
            return explicit, "explicit effort"
        target = explicit
    supported = [level for level in EFFORTS if level in supported]
    if not supported:
        return current or target, "undiscovered effort support"
    target_index = EFFORTS.index(target)
    selected = min(supported, key=lambda level: abs(EFFORTS.index(level) - target_index))
    reason = "explicit effort unsupported; closest supported effort" if explicit else "closest supported effort"
    return selected, reason


def effort_floor(target: str, workload: str) -> str:
    floors = {
        "simple": "low",
        "everyday": "medium",
        "debugging": "high",
        "architecture": "high",
        "research": "medium",
        "long_horizon": "high",
        "high_risk": "high",
    }
    floor = floors[workload]
    return EFFORTS[max(EFFORTS.index(target), EFFORTS.index(floor))]


def build_command(model: str, effort: str, prompt: str) -> list[str]:
    return [os.environ.get("AUTOROUTE_CODEX_BIN", "codex"), "-m", model, "-c", f'model_reasoning_effort="{effort}"', prompt]


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
    workload = args.workload or classify_workload(args.prompt, scores)
    if adaptive and workload in {"simple", "everyday"}:
        workload = "debugging"
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
        "dimensions": {key: {"score": scores[key], "evidence": evidence[key]} for key in DIMENSIONS},
        "discovery": {"source": source, "degraded": degraded, "model_count": len(models)},
        "availability": {
            "state_file": str(cache_path),
            "refreshed_at": state.get("refreshed_at") if state else None,
            "stale": stale,
            "inventory_changed": inventory_changed,
            "discovered_count": all_discovered_models,
            "verified_count": sum(1 for model in models if model.get("verified_available")),
            "default_model_fallback": current.get("model"),
            "models": state.get("models", {}) if state else {},
        },
        "selection": {"model_reason": model_reason, "effort_reason": effort_reason},
        "model_tier": model.get("routing_tier"),
        "model_tier_source": model.get("routing_source"),
        "adaptive_escalation": adaptive,
        "command": build_command(model["slug"], effort, args.prompt),
        "changed_current_session": False,
    }
    if mode == "manual":
        result["note"] = "Manual mode: current Codex settings are untouched."
    elif mode == "suggest":
        result["note"] = "Suggest mode: recommendation only; no new Codex session started."
    else:
        result["note"] = "Auto mode prepares a new-session command; use --run to execute it."
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Route a coding task to a discovered Codex model and reasoning effort.")
    parser.add_argument("prompt", nargs="?", default="refresh model inventory", help="Task to analyze")
    parser.add_argument("--mode", choices=["auto", "suggest", "manual"])
    parser.add_argument("--config")
    parser.add_argument("--models-file")
    parser.add_argument("--rules")
    parser.add_argument("--signals", help="JSON object of observed runtime signals")
    parser.add_argument("--scores", help="JSON object overriding semantic dimension scores (0-5)")
    parser.add_argument("--workload", choices=WORKLOADS, help="Explicit workload specialization")
    parser.add_argument("--model", help="Explicit model constraint")
    parser.add_argument("--effort", choices=EFFORTS, help="Explicit reasoning effort constraint")
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--run", action="store_true", help="Start a separate Codex process with the recommendation")
    parser.add_argument("--refresh-models", action="store_true", help="Probe every discovered model now")
    parser.add_argument("--list-models", action="store_true", help="List discovered and verified models, then exit")
    parser.add_argument("--state-file", help="Availability state JSON path")
    parser.add_argument("--ttl", type=int, default=900, help="Availability cache TTL in seconds")
    parser.add_argument("--probe-timeout", type=int, default=45, help="Per-model availability probe timeout")
    args = parser.parse_args()
    try:
        result = route(args)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    if args.list_models:
        print(json.dumps({"discovery": result["discovery"], "availability": result["availability"], "selected": {"model": result["model"], "effort": result["effort"]}}, indent=2))
        return 0
    if args.as_json:
        print(json.dumps(result, indent=2, ensure_ascii=True))
    else:
        print(f"AutoRoute: {result['model']} + {result['effort']} ({result['level']}, score {result['score']}/30)")
        print(result["note"])
        for key, value in result["dimensions"].items():
            reasons = "; ".join(value["evidence"]) or "no strong signal"
            print(f"- {key}: {value['score']}/5 ({reasons})")
        print(f"Discovery: {result['discovery']['source'] or 'fallback'}")
        print("Command: " + shlex.join(result["command"]))
    if args.run:
        if result["mode"] != "auto":
            print("Refusing --run unless mode is auto.", file=sys.stderr)
            return 2
        if not shutil.which("codex"):
            print("codex executable not found; command was printed but not run.", file=sys.stderr)
            return 2
        completed = subprocess.run(result["command"])
        if completed.returncode != 0:
            mark_model_unavailable(Path(result["availability"]["state_file"]), result["model"], f"runtime exit code {completed.returncode}")
            default_model = current_codex_config().get("model")
            default_effort = current_codex_config().get("model_reasoning_effort", "high")
            if default_model and default_model != result["model"]:
                fallback_command = build_command(default_model, default_effort, args.prompt)
                print(f"Selected model failed; retrying with default model {default_model}.", file=sys.stderr)
                return subprocess.run(fallback_command).returncode
        return completed.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
