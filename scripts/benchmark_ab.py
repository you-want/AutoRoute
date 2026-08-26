#!/usr/bin/env python3
"""Run a paired Codex control/treatment benchmark for AutoRoute."""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ROUTER = ROOT / "scripts" / "autoroute.py"
DEFAULT_TASKS = ROOT / "evals" / "ab_tasks.json"


def parse_json_answer(text: str) -> Any:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise


def route(prompt: str, scores: dict[str, int] | None = None) -> dict[str, Any]:
    command = [sys.executable, str(ROUTER), "--json"]
    if scores:
        command.extend(["--scores", json.dumps(scores, separators=(",", ":"))])
    command.append(prompt)
    completed = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    )
    return json.loads(completed.stdout)


def run_codex(prompt: str, model: str, effort: str, timeout: int) -> dict[str, Any]:
    command = [
        "codex", "exec", "--ephemeral", "--json", "--skip-git-repo-check",
        "-C", "/tmp", "-s", "read-only", "-m", model,
        "-c", f'model_reasoning_effort="{effort}"', prompt,
    ]
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
        )
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        completed = subprocess.CompletedProcess(command, 124, exc.stdout or "", exc.stderr or "timeout")
        timed_out = True
    elapsed = time.monotonic() - started
    events, invalid_lines = [], []
    for line in completed.stdout.splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            if line.strip():
                invalid_lines.append(line)
    usage = {}
    messages, errors = [], []
    for event in events:
        if event.get("type") == "turn.completed":
            usage = event.get("usage", {})
        if event.get("type") == "item.completed":
            item = event.get("item", {})
            if item.get("type") == "agent_message":
                messages.append(item.get("text", ""))
            elif item.get("type") == "error":
                errors.append(item.get("message", ""))
        if event.get("type") == "error":
            errors.append(event.get("message", ""))
    if completed.returncode != 0:
        errors.append(f"codex exited with code {completed.returncode}")
        errors.extend(line for line in completed.stderr.splitlines() if line.strip())
    if timed_out:
        errors.append("codex timed out")
    return {
        "model": model,
        "effort": effort,
        "elapsed_seconds": round(elapsed, 3),
        "exit_code": completed.returncode,
        "timed_out": timed_out,
        "usage": usage,
        "answer_text": messages[-1] if messages else "",
        "errors": errors,
        "stderr_warnings": [line for line in completed.stderr.splitlines() if line.strip()],
        "invalid_stdout_lines": invalid_lines,
    }


def score(run: dict[str, Any], expected: Any) -> float:
    if run["exit_code"] != 0 or run["timed_out"] or not run["answer_text"]:
        return 0.0
    try:
        actual = parse_json_answer(run["answer_text"])
    except (json.JSONDecodeError, TypeError):
        return 0.0
    return 1.0 if actual == expected else 0.0


def median(rows: list[dict[str, Any]], getter) -> float:
    values = [getter(row) for row in rows]
    return round(statistics.median(values), 3) if values else 0.0


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    result = {}
    for arm in ("control", "treatment"):
        selected = [row for row in rows if row["arm"] == arm]
        if not selected:
            result[arm] = {
                "runs": 0, "success_rate": None, "mean_quality": None,
                "median_elapsed_seconds": None, "total_input_tokens": 0,
                "total_cached_input_tokens": 0, "total_output_tokens": 0,
                "total_reasoning_tokens": 0, "total_elapsed_seconds": 0,
                "uncached_input_tokens": 0, "runs_with_errors": 0, "timeouts": 0,
            }
            continue
        result[arm] = {
            "runs": len(selected),
            "success_rate": round(sum(row["quality_score"] > 0 for row in selected) / len(selected), 4),
            "mean_quality": round(sum(row["quality_score"] for row in selected) / len(selected), 4),
            "median_elapsed_seconds": median(selected, lambda row: row["elapsed_seconds"]),
            "total_input_tokens": sum(row.get("usage", {}).get("input_tokens", 0) for row in selected),
            "total_cached_input_tokens": sum(row.get("usage", {}).get("cached_input_tokens", 0) for row in selected),
            "total_output_tokens": sum(row.get("usage", {}).get("output_tokens", 0) for row in selected),
            "total_reasoning_tokens": sum(row.get("usage", {}).get("reasoning_output_tokens", 0) for row in selected),
            "total_elapsed_seconds": round(sum(row["elapsed_seconds"] for row in selected), 3),
            "uncached_input_tokens": sum(row.get("usage", {}).get("input_tokens", 0) - row.get("usage", {}).get("cached_input_tokens", 0) for row in selected),
            "runs_with_errors": sum(bool(row["errors"]) for row in selected),
            "timeouts": sum(row["timed_out"] for row in selected),
        }
    control_total = result["control"]["total_input_tokens"] + result["control"]["total_output_tokens"]
    treatment_total = result["treatment"]["total_input_tokens"] + result["treatment"]["total_output_tokens"]
    result["comparison"] = {
        "control_total_tokens": control_total,
        "treatment_total_tokens": treatment_total,
        "token_change": treatment_total - control_total,
        "token_change_percent": round((treatment_total - control_total) / control_total * 100, 3) if control_total else None,
        "control_uncached_plus_output": result["control"]["uncached_input_tokens"] + result["control"]["total_output_tokens"],
        "treatment_uncached_plus_output": result["treatment"]["uncached_input_tokens"] + result["treatment"]["total_output_tokens"],
    }
    uncached_control = result["comparison"]["control_uncached_plus_output"]
    uncached_treatment = result["comparison"]["treatment_uncached_plus_output"]
    result["comparison"]["uncached_token_change"] = uncached_treatment - uncached_control
    result["comparison"]["uncached_token_change_percent"] = round((uncached_treatment - uncached_control) / uncached_control * 100, 3) if uncached_control else None
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", default=str(DEFAULT_TASKS))
    parser.add_argument("--output")
    parser.add_argument("--model", default="gpt-5.6-sol")
    parser.add_argument("--control-effort", default="high")
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    tasks = json.loads(Path(args.tasks).read_text(encoding="utf-8"))
    if args.limit:
        tasks = tasks[: args.limit]
    output = Path(args.output) if args.output else ROOT / "evals" / "results" / f"ab-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    if args.resume and output.exists():
        previous = json.loads(output.read_text(encoding="utf-8"))
        rows = previous.get("runs", [])
    completed_keys = {(row["task_id"], row["arm"]) for row in rows}
    for index, task in enumerate(tasks):
        recommendation = route(task["prompt"], task.get("scores"))
        arms = [
            ("control", args.model, args.control_effort),
            ("treatment", recommendation["model"], recommendation["effort"]),
        ]
        if index % 2:
            arms.reverse()
        for arm, model, effort in arms:
            if (task["id"], arm) in completed_keys:
                print(f"SKIP {task['id']} {arm} already completed", flush=True)
                continue
            print(f"START {task['id']} {arm} {model}+{effort}", flush=True)
            result = run_codex(task["prompt"], model, effort, args.timeout)
            result.update({
                "task_id": task["id"], "task_class": task["class"], "arm": arm,
                "quality_score": score(result, task["expected"]),
                "route": recommendation if arm == "treatment" else None,
            })
            rows.append(result)
            completed_keys.add((task["id"], arm))
            print(
                f"DONE {task['id']} {arm} quality={result['quality_score']} "
                f"tokens={result.get('usage', {}).get('input_tokens', 0) + result.get('usage', {}).get('output_tokens', 0)} "
                f"seconds={result['elapsed_seconds']}", flush=True,
            )
            output.write_text(json.dumps({"runs": rows, "summary": summarize(rows)}, indent=2), encoding="utf-8")
    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "codex_model": args.model,
        "control_effort": args.control_effort,
        "runs": rows,
        "summary": summarize(rows),
    }
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"RESULT {output}")
    print(json.dumps(report["summary"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
