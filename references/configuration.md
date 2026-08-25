# Configuration

AutoRoute reads configuration in this order (later values override earlier ones):

1. Built-in defaults: `enabled=true`, `mode=auto`.
2. A JSON file passed with `--config`.
3. `AUTOROUTE_CONFIG` (a path to a JSON file), if set.
4. `AUTOROUTE_ENABLED` and `AUTOROUTE_MODE` environment variables.
5. CLI flags such as `--mode`.

The JSON shape is deliberately small:

```json
{
  "autoroute": {
    "enabled": true,
    "mode": "suggest",
    "models_file": "/path/to/model-catalog.json"
  }
}
```

`mode` is one of `auto`, `suggest`, or `manual`. AutoRoute does not rewrite the user's Codex config as a side effect. `--run` is the explicit opt-in that starts a separate `codex` process with the selected model and effort.

Model discovery checks `models_file`, `AUTOROUTE_MODELS_FILE`, the standard `~/.codex/models_cache.json`, and then the optional `~/.codex/cc-switch-model-catalog.json`. It understands both a catalog with a `models` array and a direct array of model records. An empty or malformed catalog is reported as degraded discovery, not treated as proof that no models exist.

Portable catalogs may add `routing_tier`, `capability_tier`, or `quality_tier` to a model record. Accepted values are `low`, `medium`, `high`, `xhigh`, `max`, or the equivalent zero-based integer. AutoRoute prefers this metadata over guessing capability from a model name.
