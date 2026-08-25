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
from pathlib import Path
from typing import Any


EFFORTS = ["none", "low", "medium", "high", "xhigh", "max"]
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


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def load_config(path_arg: str | None) -> dict[str, Any]:
    config: dict[str, Any] = {"enabled": True, "mode": "auto"}
    candidates = []
    if path_arg:
        candidates.append(Path(path_arg).expanduser())
    elif os.environ.get("AUTOROUTE_CONFIG"):
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
        models.append({
            "slug": str(slug),
            "display_name": str(item.get("display_name") or slug),
            "reasoning": levels,
            "priority": int(item.get("priority", 10000)) if str(item.get("priority", "")).lstrip("-").isdigit() else 10000,
            "context_window": item.get("context_window"),
            "routing_tier": item.get("routing_tier") or item.get("capability_tier") or item.get("quality_tier"),
        })
    return models


def discover_models(config: dict[str, Any], explicit: str | None) -> tuple[list[dict[str, Any]], str | None, bool]:
    for path in catalog_paths(config, explicit):
        raw = read_json(path)
        models = normalize_models(raw)
        if models:
            return models, str(path), False
    return [], None, True


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


def choose_model(models: list[dict[str, Any]], level: str, current: str | None, explicit: str | None) -> tuple[dict[str, Any], str]:
    if explicit:
        for model in models:
            if model["slug"] == explicit:
                return model, "explicit model"
        return {"slug": explicit, "display_name": explicit, "reasoning": [], "priority": 0, "routing_tier": None}, "explicit model (not discovered)"
    target_tier = LEVELS.index(level) if level in LEVELS else 2
    tiered = []
    for model in models:
        tier = model.get("routing_tier")
        if isinstance(tier, str) and tier.lower() in LEVELS:
            tiered.append((abs(LEVELS.index(tier.lower()) - target_tier), model["priority"], model))
        elif isinstance(tier, int) and 0 <= tier < len(LEVELS):
            tiered.append((abs(tier - target_tier), model["priority"], model))
    if tiered:
        return sorted(tiered, key=lambda item: (item[0], item[1]))[0][2], "catalog routing tier"
    token = "luna" if level == "low" else "terra" if level == "medium" else "sol"
    matching = [model for model in models if token in model["slug"].lower()]
    if matching:
        return sorted(matching, key=lambda model: model["priority"])[0], f"{token} tier heuristic"
    if level in {"low", "medium"}:
        tier_words = ("luna", "terra", "sol", "mini", "nano", "pro", "max", "opus", "flash", "haiku")
        balanced = [model for model in models if not any(word in model["slug"].lower() for word in tier_words)]
        if balanced:
            return sorted(balanced, key=lambda model: model["priority"])[0], "generic balanced-model fallback"
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


def build_command(model: str, effort: str, prompt: str) -> list[str]:
    return ["codex", "-m", model, "-c", f'model_reasoning_effort="{effort}"', prompt]


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
    models, source, degraded = discover_models(config, args.models_file)
    current = current_codex_config()
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
    model_constraint = args.model or (current.get("model") if mode == "manual" else None)
    effort_constraint = args.effort or (current.get("model_reasoning_effort") if mode == "manual" else None)
    model, model_reason = choose_model(models, level, current.get("model"), model_constraint)
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
        "dimensions": {key: {"score": scores[key], "evidence": evidence[key]} for key in DIMENSIONS},
        "discovery": {"source": source, "degraded": degraded, "model_count": len(models)},
        "selection": {"model_reason": model_reason, "effort_reason": effort_reason},
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
    parser.add_argument("prompt", help="Task to analyze")
    parser.add_argument("--mode", choices=["auto", "suggest", "manual"])
    parser.add_argument("--config")
    parser.add_argument("--models-file")
    parser.add_argument("--rules")
    parser.add_argument("--signals", help="JSON object of observed runtime signals")
    parser.add_argument("--scores", help="JSON object overriding semantic dimension scores (0-5)")
    parser.add_argument("--model", help="Explicit model constraint")
    parser.add_argument("--effort", choices=EFFORTS, help="Explicit reasoning effort constraint")
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--run", action="store_true", help="Start a separate Codex process with the recommendation")
    args = parser.parse_args()
    try:
        result = route(args)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
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
        return subprocess.run(result["command"]).returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
