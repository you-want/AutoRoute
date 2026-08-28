import pytest

from scripts.router_policy import analyze, band_for, classify_workload, effort_floor, task_score


def test_analyze_scores_debugging_and_scope_signals():
    scores, evidence = analyze(
        "Debug a flaky bug across multiple modules",
        {"test_failures": 2, "changed_files": 8},
    )
    assert scores["complexity"] >= 3
    assert scores["scope"] >= 3
    assert scores["iteration"] >= 3
    assert "reported test failures" in evidence["iteration"]


def test_score_bands_and_workload_floors():
    assert task_score({dimension: 0 for dimension in ("complexity", "scope", "reasoning", "risk", "context", "iteration")}) == 0
    assert band_for(19) == ("xhigh", "xhigh")
    assert classify_workload("Compare implementation options", {"risk": 0}) == "research"
    assert effort_floor("low", "debugging") == "high"


def test_invalid_score_override_is_rejected():
    with pytest.raises(ValueError, match="from 0 to 5"):
        analyze("Plan", {}, {"risk": 6})
