---
name: autoroute
description: Automatically analyze coding tasks and recommend the best available Codex model and reasoning effort. Use for model routing, effort selection, cost/quality tradeoffs, or adaptive escalation; do not use for ordinary coding tasks when routing is not requested.
metadata:
  short-description: Route coding tasks to the right model
---

# AutoRoute

AutoRoute is a routing layer for Codex coding work. Its goal is to minimize total cost for the required quality, including retries and failure recovery—not merely to minimize reasoning tokens.

## Operating contract

- Discover available models and supported reasoning levels at runtime. Do not assume that a model named in documentation or an old catalog is available to this user.
- On first use or after the availability TTL, run the lightweight model probes and cache their results. Route only to verified models; if a selected model execution fails, retry once with the user's configured default model and effort.
- Respect `autoroute.enabled` and `autoroute.mode` when a project or user config provides them. `manual` leaves the current Codex settings alone; `suggest` reports a recommendation; `auto` may prepare a new-session invocation.
- Preserve an explicitly requested model or effort. Treat it as a user constraint and explain any incompatibility instead of silently overriding it.
- Analyze six dimensions: complexity, scope, reasoning demand, risk, context size, and iteration horizon. Record concise evidence for each score.
- Select model and reasoning effort independently. Choose the closest supported effort; never emit an unsupported effort.
- With multiple models, prefer discovered capability metadata; otherwise use the conservative family defaults in `references/routing.md`. Select only models present in the discovered catalog.
- Use workload specialization when it is explicit: long-horizon work may prefer GPT-5.2, while complex coding/debugging remains on Sol; if the specialized model is unavailable, fall back to the nearest available tier.
- If signals show failed tests, repeated retries, cross-language changes, or a larger-than-expected diff, apply adaptive escalation and explain the trigger.
- A Codex Skill cannot change the model of an already-running conversation. When execution is requested, use the CLI helper to start a new Codex session and make that boundary explicit.
- If discovery fails, fall back to the current configured model and a supported/default effort, and mark the recommendation as degraded.
- Keep AutoRoute state separate from `CODEX_HOME`: use the platform user cache by default, honor `AUTOROUTE_CACHE_DIR`/`cache_dir`, and fall back to a temporary cache if needed. Cache persistence must never abort routing.
- Treat live `codex exec` probes as optional capability checks. If app-server startup is blocked by a sandbox or permissions policy, retain the discovered inventory, report `probe_status=blocked`, and route using catalog/current-model metadata instead of declaring every model unavailable.

## Use the helper

When Codex itself can inspect the request or repository, first make a semantic 0–5 judgment for all six dimensions, then pass those values with `--scores`. Use prompt-only analysis as the fallback for standalone CLI use. Run `scripts/autoroute.py --help` for the complete interface. Typical calls:

```bash
python3 scripts/autoroute.py "Add a loading state to Button"
python3 scripts/autoroute.py --mode suggest --json "Debug intermittent React state desync"
python3 scripts/autoroute.py --scores '{"complexity":4,"scope":3,"reasoning":5,"risk":2,"context":3,"iteration":4}' "Investigate and fix the issue"
python3 scripts/autoroute.py --workload research "Compare these implementation options"
python3 scripts/autoroute.py --signals '{"test_failures":2,"changed_files":14}' "Refactor the sync layer"
python3 scripts/autoroute.py --mode auto --run "Implement the approved repository-wide migration"
```

Use `references/configuration.md` for config precedence, `references/routing.md` for the scoring and fallback model, and `references/evaluation.md` when measuring impact. The bundled `rules/default.json` and `evals/cases.json` are baseline, editable project resources rather than claims about any particular provider's current catalog. Do not treat a keyword match as certainty: report the evidence and use the catalog's supported capabilities as the final constraint.

## Response shape in conversation

When routing a user task directly, return: selected model, selected effort, task level/score, six dimension scores with evidence, discovery source, mode behavior, and any adaptive escalation. Keep this routing result compact; do not add an unrelated domain persona, report template, or extra content layer. If the current mode is `suggest`, ask before starting another session. If it is `manual`, give only an informational recommendation and do not imply that settings changed.
