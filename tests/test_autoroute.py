#!/usr/bin/env python3
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "autoroute.py"
CATALOG = ROOT / "tests" / "catalog.json"


def run(prompt, *extra):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--models-file", str(CATALOG), "--json", *extra, prompt],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def main():
    low = run("Add a loading state to the Button component")
    assert low["level"] == "low", low
    assert low["model"] == "gpt-5.6-luna", low
    assert low["effort"] == "low", low

    high = run("Debug an intermittent React state synchronization bug across the page")
    assert high["level"] == "high", high
    assert high["model"] == "gpt-5.6-sol", high

    no_tier = ROOT / "tests" / "no_tier_catalog.json"
    no_tier.write_text(json.dumps({"models": [
        {"slug": "gpt-5.6", "priority": 1, "supported_reasoning_levels": ["none", "high"]},
        {"slug": "gpt-5.6-sol", "priority": 2, "supported_reasoning_levels": ["none", "high"]},
    ]}), encoding="utf-8")
    try:
        conservative = subprocess.run(
            [sys.executable, str(SCRIPT), "--models-file", str(no_tier), "--json", "Add a loading state"],
            check=True, capture_output=True, text=True,
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

    semantic = run(
        "Plan the change",
        "--scores",
        '{"complexity":5,"scope":5,"reasoning":5,"risk":3,"context":4,"iteration":5}',
    )
    assert semantic["level"] == "max", semantic
    assert semantic["model"] == "gpt-5.6-sol", semantic

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
    print("autoroute tests passed")


if __name__ == "__main__":
    main()
