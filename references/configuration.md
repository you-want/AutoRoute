# Configuration

AutoRoute reads configuration in this order (later values override earlier ones):

1. Built-in defaults: `enabled=true`, `mode=auto`.
2. `~/.codex/autoroute.json`, when present.
3. A JSON file passed with `--config`.
4. `AUTOROUTE_CONFIG` (a path to a JSON file), if set.
5. `AUTOROUTE_ENABLED` and `AUTOROUTE_MODE` environment variables.
6. CLI flags such as `--mode`.

The JSON shape is deliberately small:

```json
{
  "autoroute": {
    "enabled": true,
    "mode": "suggest",
    "models_file": "/path/to/model-catalog.json",
    "models": []
  }
}
```

`mode` is one of `auto`, `suggest`, or `manual`. AutoRoute does not rewrite the user's Codex config as a side effect. `--run` is the explicit opt-in that starts a separate `codex` process with the selected model and effort.

Model discovery checks `models_file`, `AUTOROUTE_MODELS_FILE`, the standard `~/.codex/models_cache.json`, and then the optional `~/.codex/cc-switch-model-catalog.json`. It understands both a catalog with a `models` array and a direct array of model records. An empty or malformed catalog is reported as degraded discovery, not treated as proof that no models exist.

An explicit `autoroute.models` inventory takes precedence over cache discovery. Copy `rules/codex-models.example.json` to `~/.codex/autoroute.json` and remove models or efforts that your Codex account/provider cannot actually launch. This makes routing deterministic when the local model cache is incomplete or stale.

If no explicit inventory is present, AutoRoute treats catalog tiers inferred only from model names as untrusted and keeps the current configured model. This is safer for custom providers whose model list may contain unavailable channels.

Portable catalogs may add `routing_tier`, `capability_tier`, or `quality_tier` to a model record. Accepted values are `low`, `medium`, `high`, `xhigh`, `max`, or the equivalent zero-based integer. AutoRoute prefers this metadata over guessing capability from a model name.

When those fields are absent, the fallback family map is: `luna` -> low, `terra` -> medium, `sol` and `gpt-5.5` -> high, and `gpt-5.2` -> medium/long-horizon. This map is applied only to models present in the discovered catalog; AutoRoute never invents an unavailable model.
