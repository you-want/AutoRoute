#!/usr/bin/env python3
import json
import subprocess
import sys
import tempfile
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "autoroute.py"
CATALOG = ROOT / "tests" / "catalog.json"
sys.path.insert(0, str(ROOT))
from scripts.benchmark_ab import summarize


def run(prompt, *extra):
    environment = dict(os.environ)
    environment["AUTOROUTE_SKIP_PROBE"] = "1"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--models-file", str(CATALOG), "--json", *extra, prompt],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    return json.loads(result.stdout)


def test_cli_and_routing_end_to_end():
    partial_summary = summarize([{
        "arm": "control",
        "elapsed_seconds": 0.1,
        "quality_score": 1.0,
        "errors": [],
        "timed_out": False,
        "usage": {"input_tokens": 2, "output_tokens": 3},
    }])
    assert partial_summary["treatment"]["runs"] == 0, partial_summary
    assert partial_summary["comparison"]["treatment_uncached_plus_output"] == 0, partial_summary

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as file_handle:
        isolated_config = Path(file_handle.name)
        json.dump({"autoroute": {"models": [{
            "slug": "host-only", "routing_tier": "high", "supported_reasoning_levels": ["high"]
        }]}}, file_handle)
    try:
        isolated = run("Add a loading state", "--config", str(isolated_config))
        assert isolated["discovery"]["model_count"] == 5, isolated
        assert "host-only" not in isolated["availability"]["models"], isolated
    finally:
        isolated_config.unlink(missing_ok=True)

    low = run("Add a loading state to the Button component")
    assert low["level"] == "low", low
    assert low["model"] == "gpt-5.6-luna", low
    assert low["effort"] == "low", low

    high = run("Debug an intermittent React state synchronization bug across the page")
    assert high["level"] == "high", high
    assert high["model"] == "gpt-5.6-sol", high

    medium = run(
        "Implement a small API endpoint with validation",
        "--scores",
        '{"complexity":2,"scope":2,"reasoning":2,"risk":1,"context":1,"iteration":2}',
    )
    assert medium["level"] == "medium", medium
    assert medium["model"] == "gpt-5.6-terra", medium
    assert medium["effort"] == "medium", medium

    inferred_everyday = run("Implement a settings form with validation")
    assert inferred_everyday["workload"] == "everyday", inferred_everyday
    assert inferred_everyday["workload_source"] == "inferred", inferred_everyday
    assert inferred_everyday["model"] == "gpt-5.6-terra", inferred_everyday

    explicit_workload = run("Implement a settings form with validation", "--workload", "research")
    assert explicit_workload["workload"] == "research", explicit_workload
    assert explicit_workload["workload_source"] == "cli", explicit_workload

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as file_handle:
        workload_config = Path(file_handle.name)
        json.dump({"autoroute": {"workload": "architecture"}}, file_handle)
    try:
        configured_workload = run("Implement a settings form with validation", "--config", str(workload_config))
        assert configured_workload["workload"] == "architecture", configured_workload
        assert configured_workload["workload_source"] == "config", configured_workload
    finally:
        workload_config.unlink(missing_ok=True)

    long_horizon = run(
        "Design a multi-quarter repository modernization roadmap",
        "--scores",
        '{"complexity":4,"scope":4,"reasoning":4,"risk":2,"context":5,"iteration":5}',
    )
    assert long_horizon["model"] == "gpt-5.2", long_horizon
    assert long_horizon["effort"] == "xhigh", long_horizon

    catalog_without_52 = json.loads(CATALOG.read_text(encoding="utf-8"))
    catalog_without_52["models"] = [model for model in catalog_without_52["models"] if model["slug"] != "gpt-5.2"]
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as file_handle:
        long_horizon_without_52 = Path(file_handle.name)
        json.dump(catalog_without_52, file_handle)
    try:
        fallback = subprocess.run(
            [sys.executable, str(SCRIPT), "--models-file", str(long_horizon_without_52), "--json", "--scores",
             '{"complexity":4,"scope":4,"reasoning":4,"risk":2,"context":5,"iteration":5}',
             "Design a multi-quarter repository modernization roadmap"], check=True, capture_output=True, text=True,
            env={**os.environ, "AUTOROUTE_SKIP_PROBE": "1"},
        )
        assert json.loads(fallback.stdout)["model"] == "gpt-5.6-sol", fallback.stdout
    finally:
        long_horizon_without_52.unlink(missing_ok=True)

    no_tier = ROOT / "tests" / "no_tier_catalog.json"
    no_tier.write_text(json.dumps({"models": [
        {"slug": "gpt-5.6", "priority": 1, "supported_reasoning_levels": ["none", "high"]},
        {"slug": "gpt-5.6-sol", "priority": 2, "supported_reasoning_levels": ["none", "high"]},
    ]}), encoding="utf-8")
    try:
        conservative = subprocess.run(
            [sys.executable, str(SCRIPT), "--models-file", str(no_tier), "--json", "Add a loading state"],
            check=True, capture_output=True, text=True, env={**os.environ, "AUTOROUTE_SKIP_PROBE": "1"},
        )
        assert json.loads(conservative.stdout)["model"] == "gpt-5.6-sol", conservative.stdout
    finally:
        no_tier.unlink(missing_ok=True)

    escalated = run(
        "Refactor the sync layer",
        "--signals",
        '{"test_failures":2,"retry_count":3,"changed_files":14}',
    )
    assert escalated["effort"] == "high", escalated
    assert escalated["model"] == "gpt-5.6-sol", escalated
    assert escalated["adaptive_escalation"] is True, escalated

    explicit = run("Add a loading state", "--model", "gpt-5.6-sol", "--effort", "xhigh")
    assert explicit["model"] == "gpt-5.6-sol", explicit
    assert explicit["effort"] == "xhigh", explicit

    unknown_effort = run("Add a loading state", "--model", "unknown-model", "--effort", "xhigh")
    assert unknown_effort["effort"] == "xhigh", unknown_effort
    assert "undiscovered" in unknown_effort["selection"]["effort_reason"], unknown_effort

    rejected_session = subprocess.run(
        [sys.executable, str(SCRIPT), "--models-file", str(CATALOG), "--mode", "suggest", "--session", "--json", "task"],
        capture_output=True, text=True,
        env={**os.environ, "AUTOROUTE_SKIP_PROBE": "1", "CODEX_THREAD_ID": "", "CODEX_SESSION_ID": ""},
    )
    assert rejected_session.returncode == 2, rejected_session.stderr

    semantic = run(
        "Plan the change",
        "--scores",
        '{"complexity":5,"scope":5,"reasoning":5,"risk":3,"context":4,"iteration":5}',
    )
    assert semantic["level"] == "max", semantic
    assert semantic["model"] == "gpt-5.2", semantic

    disabled_config = ROOT / "tests" / "disabled.json"
    disabled_config.write_text('{"autoroute": {"enabled": false, "mode": "auto"}}', encoding="utf-8")
    try:
        disabled = run("Rewrite the parser", "--config", str(disabled_config))
        assert disabled["mode"] == "manual", disabled
        assert disabled["enabled"] is False, disabled
    finally:
        disabled_config.unlink(missing_ok=True)

    manual = run("Rewrite the parser", "--mode", "manual")
    assert manual["changed_current_session"] is False, manual
    assert "untouched" in manual["note"], manual

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_dir = Path(temp_dir)
        probe_catalog = temp_dir / "catalog.json"
        probe_catalog.write_text(json.dumps({"models": [
            {"slug": "gpt-good", "routing_tier": "low", "priority": 1, "supported_reasoning_levels": ["none", "low"]},
            {"slug": "gpt-bad", "routing_tier": "medium", "priority": 2, "supported_reasoning_levels": ["none", "medium"]},
        ]}), encoding="utf-8")
        state_file = temp_dir / "state.json"
        probe = subprocess.run(
            [sys.executable, str(SCRIPT), "--models-file", str(probe_catalog), "--state-file", str(state_file),
             "--refresh-models", "--list-models", "--json", "probe"],
            check=True, capture_output=True, text=True,
            env={**os.environ, "AUTOROUTE_CODEX_BIN": str(ROOT / "tests" / "fake_codex.py")},
        )
        listing = json.loads(probe.stdout)
        state = json.loads(state_file.read_text(encoding="utf-8"))
        assert listing["discovery"]["model_count"] == 2, listing
        assert set(listing["availability"]["models"]) == {"gpt-good", "gpt-bad"}, listing
        assert state["models"]["gpt-good"]["available"] is True, state
        assert state["models"]["gpt-bad"]["available"] is False, state
        assert listing["selected"]["model"] == "gpt-good", listing

        blocked_parent = temp_dir / "blocked"
        blocked_parent.write_text("not a directory", encoding="utf-8")
        fallback_state = blocked_parent / "state.json"
        fallback_probe = subprocess.run(
            [sys.executable, str(SCRIPT), "--models-file", str(probe_catalog),
             "--state-file", str(fallback_state), "--refresh-models", "--json", "probe"],
            check=True, capture_output=True, text=True,
            env={**os.environ, "AUTOROUTE_CODEX_BIN": str(ROOT / "tests" / "fake_codex.py")},
        )
        fallback_result = json.loads(fallback_probe.stdout)
        assert fallback_result["model"] == "gpt-good", fallback_result
        assert fallback_result["availability"]["state_file"] != str(fallback_state), fallback_result

        blocked_state = temp_dir / "blocked-state.json"
        blocked_probe = subprocess.run(
            [sys.executable, str(SCRIPT), "--models-file", str(probe_catalog),
             "--state-file", str(blocked_state), "--refresh-models", "--json",
             "--model", "gpt-good", "probe"],
            check=True, capture_output=True, text=True,
            env={**os.environ, "AUTOROUTE_CODEX_BIN": str(ROOT / "tests" / "blocked_codex.py")},
        )
        blocked_result = json.loads(blocked_probe.stdout)
        assert blocked_result["model"] == "gpt-good", blocked_result
        assert blocked_result["availability"]["probe_status"] == "blocked", blocked_result
    # Keep the assertions in one end-to-end test for now. The next refactor
    # can split pure routing cases from subprocess/integration cases safely.


if __name__ == "__main__":
    test_cli_and_routing_end_to_end()
    print("autoroute tests passed")
