"""Pure routing policy helpers.

This module intentionally has no filesystem, subprocess, or Codex-session
side effects. Keeping policy here makes it straightforward to unit test and
safe to reuse from other frontends.
"""

from __future__ import annotations

import re
from typing import Any


DEFAULT_WEIGHTS = {
    "complexity": 1.2,
    "scope": 1.0,
    "reasoning": 1.2,
    "risk": 1.2,
    "context": 0.8,
    "iteration": 1.0,
}
EFFORTS = ["none", "low", "medium", "high", "xhigh", "max", "ultra"]
BANDS = [
    ("low", 0, 6, "low"),
    ("medium", 7, 12, "medium"),
    ("high", 13, 18, "high"),
    ("xhigh", 19, 24, "xhigh"),
    ("max", 25, 30, "max"),
]
DIMENSIONS = tuple(DEFAULT_WEIGHTS)
LEVELS = ["low", "medium", "high", "xhigh", "max"]


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
    for key, dimension, amount, reason in ((
        ("test_failures", "iteration", 2, "reported test failures"),
        ("retry_count", "iteration", 1, "repeated retries"),
        ("changed_files", "scope", 2, "large observed diff"),
        ("cross_language", "scope", 2, "cross-language change"),
        ("production_risk", "risk", 2, "elevated runtime risk"),
    )):
        try:
            active = max(0, int(signals.get(key, 0))) > 0
        except (TypeError, ValueError):
            active = signals.get(key) is True
        if active:
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


def effort_floor(target: str, workload: str) -> str:
    floors = {"simple": "low", "everyday": "medium", "debugging": "high", "architecture": "high", "research": "medium", "long_horizon": "high", "high_risk": "high"}
    floor = floors[workload]
    return EFFORTS[max(EFFORTS.index(target), EFFORTS.index(floor))]
