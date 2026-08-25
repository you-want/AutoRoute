# Routing policy

The analyzer scores each dimension from 0 to 5:

| Dimension | What raises the score |
| --- | --- |
| Complexity | architecture, concurrency, distributed behavior, ambiguous root causes |
| Scope | multiple files/modules, repository-wide or cross-repository changes |
| Reasoning | debugging, design, tradeoffs, research, algorithmic work |
| Risk | security, data loss, migrations, production or compatibility impact |
| Context | large codebase, many artifacts, logs, specifications, or long prompts |
| Iteration | long-horizon work, repeated validation, many dependent steps |

The weighted score is normalized to 0–30. Baseline bands are: 0–6 Low, 7–12 Medium, 13–18 High, 19–24 XHigh, and 25–30 Max. The effort target is derived from the band, then clamped to the model's discovered supported levels.

Model choice is independent of effort. Selection order is: explicit user constraint, catalog `routing_tier`/`capability_tier`/`quality_tier`, the current configured model when no tier metadata exists, recognizable family-tier names, a generic balanced model, then catalog priority. Name matching is therefore only a fallback. Catalog metadata and explicit user constraints win. This conservative order avoids selecting a stale cached alias when the active Codex setup already has a known-working model.

The CLI's built-in analyzer is deterministic and intentionally conservative. When a capable Agent has inspected the actual task or repository, it can pass semantic dimension judgments through `--scores`, for example `--scores '{"complexity":4,"scope":5,"reasoning":4,"risk":2,"context":3,"iteration":4}'`. Each override must be 0–5 and is shown as explicit evidence in the output. This separates semantic task understanding from deterministic routing.

Adaptive signals (`test_failures`, `retry_count`, `changed_files`, `cross_language`, `production_risk`) can raise the effort target or model tier. Adaptive routing never lowers an explicitly requested model/effort.
