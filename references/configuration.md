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

`mode` is one of `auto`, `suggest`, or `manual`. AutoRoute does not rewrite the user's Codex config as a side effect. The default `auto` behavior analyzes the coding task; the routed launcher applies the result when the new Codex process starts. `suggest` and `manual` never change launch settings. `--run` explicitly starts a separate `codex` process with the selected model and effort. Codex does not provide a supported API for hot-switching an already-running conversation.

On first use, or when the availability cache is older than `ttl` (default 900 seconds), AutoRoute runs a minimal read-only `codex exec` probe for each discovered model and stores results in the platform user cache (`~/Library/Caches/codex` on macOS, `$XDG_CACHE_HOME/codex` or `~/.cache/codex` on Linux). It never uses `CODEX_HOME` for AutoRoute state by default. Set `AUTOROUTE_CACHE_DIR` or `autoroute.cache_dir` to override the cache directory. If the selected directory is unavailable, it falls back to the system temporary directory and continues routing. Use `--state-file` only when an exact state file path is required. Use `--refresh-models` to force a refresh, `--list-models` to inspect the state, and `--ttl 0` to probe every invocation. Set `AUTOROUTE_SKIP_PROBE=1` only for offline tests.

Live probes are optional. When Codex app-server startup is blocked by a sandbox or permissions policy, the result reports `availability.probe_status: "blocked"` and keeps the discovered model inventory for routing. This avoids treating an execution-policy limitation as evidence that models are unavailable.

`scripts/codex-with-autoroute` checks the inventory before launching the normal terminal Codex CLI. Candidate discovery runs every launch; live probes run only after an inventory change or TTL expiry. It is opt-in because a Skill cannot safely rewrite a user's shell aliases or intercept every Codex surface (desktop, IDE, cloud, and CLI) by itself.

Model discovery checks `models_file`, `AUTOROUTE_MODELS_FILE`, the standard `~/.codex/models_cache.json`, and then the optional `~/.codex/cc-switch-model-catalog.json`. An explicitly supplied catalog (`--models-file`, `autoroute.models_file`, or `AUTOROUTE_MODELS_FILE`) is treated as an isolated inventory and is not merged with host-wide caches; this keeps custom-provider routing and tests reproducible. Auto-discovery without an explicit catalog still checks the standard caches. The loader understands both a catalog with a `models` array and a direct array of model records. An empty or malformed catalog is reported as degraded discovery, not treated as proof that no models exist.

An explicit `autoroute.models` inventory takes precedence over cache discovery. Copy `rules/codex-models.example.json` to `~/.codex/autoroute.json` and remove models or efforts that your Codex account/provider cannot actually launch. This makes routing deterministic when the local model cache is incomplete or stale.

If no explicit inventory is present, AutoRoute treats catalog tiers inferred only from model names as untrusted and keeps the current configured model when that model is present in the discovered catalog. If it is absent, routing falls back to the available catalog's workload/family and priority rules. This is safer for custom providers whose model list may contain unavailable channels.

Portable catalogs may add `routing_tier`, `capability_tier`, or `quality_tier` to a model record. Accepted values are `low`, `medium`, `high`, `xhigh`, `max`, or the equivalent zero-based integer. AutoRoute prefers this metadata over guessing capability from a model name.

When those fields are absent, the fallback family map is: `luna` -> low, `terra` -> medium, `sol` and `gpt-5.5` -> high, and `gpt-5.2` -> medium/long-horizon. This map is applied only to models present in the discovered catalog; AutoRoute never invents an unavailable model.
