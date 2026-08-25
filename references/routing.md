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

Model choice is independent of effort. Selection order is: explicit user constraint, explicit catalog tier, workload-family preference, current configured model as a safety fallback, then catalog priority. If the catalog has only name-inferred tiers and no explicit tier metadata, preserve the current configured model; cached aliases are not proof of a working provider channel. The built-in family preference treats Luna as the fast/low-cost tier, Terra as the balanced tier, Sol as the frontier tier, GPT-5.5 as a frontier fallback, and GPT-5.2 as a long-horizon professional fallback. A model must exist in the discovered catalog before it can be selected.

For long-horizon work (`context` or `iteration` score >= 4), GPT-5.2 is preferred when its exact catalog entry is available; otherwise the normal high-capability fallback selects Sol, then GPT-5.5. For high-risk, architecture, research-heavy, or difficult debugging work, Sol remains the default frontier choice. Effort is selected separately and clamped to the chosen model's supported levels.

The CLI's built-in analyzer is deterministic and intentionally conservative. When a capable Agent has inspected the actual task or repository, it can pass semantic dimension judgments through `--scores`, for example `--scores '{"complexity":4,"scope":5,"reasoning":4,"risk":2,"context":3,"iteration":4}'`. Each override must be 0–5 and is shown as explicit evidence in the output. This separates semantic task understanding from deterministic routing.

Workload specialization can be inferred or supplied explicitly: `simple`, `everyday`, `debugging`, `architecture`, `research`, `long_horizon`, or `high_risk`. It is a second axis alongside difficulty; a medium-difficulty research task can prefer GPT-5.5, while a long-horizon task can prefer GPT-5.2 if available.

Adaptive signals (`test_failures`, `retry_count`, `changed_files`, `cross_language`, `production_risk`) can raise the effort target or model tier. Adaptive routing never lowers an explicitly requested model/effort.
