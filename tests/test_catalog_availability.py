import json
import time
from pathlib import Path

from scripts.availability import filter_verified_models, load_model_state
from scripts.model_catalog import discover_models, normalize_models


EFFORTS = ["none", "low", "medium", "high", "xhigh", "max", "ultra"]
LEVELS = ["low", "medium", "high", "max"]


def test_empty_and_malformed_catalogs_are_degraded():
    assert normalize_models([], EFFORTS, LEVELS) == []
    assert normalize_models({"models": [{"display_name": "missing slug"}, "bad"]}, EFFORTS, LEVELS) == []
    models, source, degraded = discover_models({}, "/does/not/exist", lambda _: None, EFFORTS, LEVELS)
    assert models == [] and source is None and degraded is True


def test_catalog_normalization_deduplicates_and_filters_effort():
    models = normalize_models({"models": [
        {"id": "custom", "supported_reasoning_levels": ["low", "unsupported", {"effort": "high"}]},
        {"slug": "custom", "priority": 1},
    ]}, EFFORTS, LEVELS)
    assert len(models) == 1
    assert models[0]["reasoning"] == ["low", "high"]


def test_corrupt_and_expired_cache_are_unusable(tmp_path: Path):
    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("not json", encoding="utf-8")
    assert load_model_state(corrupt, 900, lambda p: None) == (None, True)
    expired = tmp_path / "expired.json"
    expired.write_text(json.dumps({"models": {}, "refreshed_epoch": time.time() - 100}), encoding="utf-8")
    state, stale = load_model_state(expired, 10, lambda p: json.loads(p.read_text(encoding="utf-8")))
    assert state is not None and stale is True


def test_partial_probe_keeps_verified_and_default_fallback():
    models = [{"slug": "good"}, {"slug": "bad"}]
    state = {"probe_status": "partial", "models": {"good": {"available": True}, "bad": {"available": False}}}
    assert [m["slug"] for m in filter_verified_models(models, state, "good")] == ["good"]
    assert [m["slug"] for m in filter_verified_models(models, state, "missing")] == ["good"]
    blocked = {"probe_status": "blocked", "models": {"good": {"available": False}}}
    assert filter_verified_models(models, blocked, None) == models
