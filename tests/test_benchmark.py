import json
import subprocess
import sys
from pathlib import Path

from scripts.benchmark_ab import run_codex, summarize


ROOT = Path(__file__).resolve().parents[1]


def make_run(arm, task_class, quality_score, input_tokens=10, cached_input_tokens=2, output_tokens=5):
    return {
        "arm": arm,
        "task_class": task_class,
        "elapsed_seconds": 1.0,
        "quality_score": quality_score,
        "errors": [],
        "timed_out": False,
        "usage": {
            "input_tokens": input_tokens,
            "cached_input_tokens": cached_input_tokens,
            "output_tokens": output_tokens,
            "reasoning_output_tokens": 1,
        },
    }


def test_summarize_includes_class_breakdown_and_quality_adjusted_cost():
    rows = [
        make_run("control", "simple", 1.0),
        make_run("control", "simple", 0.0),
        make_run("treatment", "simple", 1.0, input_tokens=8, cached_input_tokens=3, output_tokens=4),
    ]
    summary = summarize(rows)

    assert summary["by_class"]["simple"]["control"]["runs"] == 2
    assert summary["by_class"]["simple"]["treatment"]["runs"] == 1
    # Control uncached+output is 13+13=26; total quality is 1, so cost is 26.
    assert summary["control"]["quality_adjusted_cost"] == 26.0
    assert summary["treatment"]["quality_adjusted_cost"] == 9.0


def test_run_codex_accepts_custom_binary(tmp_path):
    fake = tmp_path / "fake-codex"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'{\\\"ok\\\":true}'}}))\n"
        "print(json.dumps({'type':'turn.completed','usage':{'input_tokens':2,'output_tokens':3}}))\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)

    result = run_codex("return json", "test-model", "low", 10, str(fake))

    assert result["exit_code"] == 0
    assert result["answer_text"] == '{"ok":true}'
    assert result["usage"]["output_tokens"] == 3


def test_min_per_class_rejects_small_task_file(tmp_path):
    tasks = tmp_path / "tasks.json"
    tasks.write_text(
        json.dumps([{
            "id": "one",
            "class": "simple",
            "prompt": "noop",
            "expected": {},
        }]),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "benchmark_ab.py"), "--tasks", str(tasks), "--min-per-class", "2"],
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert "Need at least 2 tasks per class" in completed.stderr


def test_cli_passes_models_file_and_codex_bin_to_report(tmp_path):
    output = tmp_path / "report.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "benchmark_ab.py"),
            "--tasks",
            str(ROOT / "evals" / "ab_tasks.json"),
            "--models-file",
            str(ROOT / "tests" / "catalog.json"),
            "--codex-bin",
            str(ROOT / "tests" / "fake_codex.py"),
            "--limit",
            "1",
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        env={"AUTOROUTE_SKIP_PROBE": "1", "PATH": str(Path(sys.executable).parent)},
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["models_file"] == str(ROOT / "tests" / "catalog.json")
    assert report["codex_bin"] == str(ROOT / "tests" / "fake_codex.py")
