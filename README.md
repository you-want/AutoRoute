# AutoRoute

AutoRoute is a Codex Skill and a small standard-library CLI for adaptive model routing:

> Automatically select the right available model and reasoning effort for each coding task.

It discovers the local Codex model catalog, scores complexity/scope/reasoning/risk/context/iteration, and emits a recommendation. It supports `auto`, `suggest`, and `manual` modes. It never rewrites the active Codex configuration. `--run` explicitly starts a separate Codex session with the selected flags because a Skill cannot change the model of an already-running conversation.

## Quick start

```bash
python3 scripts/autoroute.py "Add a loading state to the Button component"
python3 scripts/autoroute.py --mode suggest --json "Debug intermittent React state desync"
python3 scripts/autoroute.py --signals '{"test_failures":2,"changed_files":14}' "Refactor the sync layer"
python3 scripts/autoroute.py --scores '{"complexity":4,"scope":5,"reasoning":4,"risk":2,"context":3,"iteration":4}' "Plan the migration"
python3 scripts/autoroute.py --workload research "Compare these implementation options"
python3 scripts/autoroute.py --mode auto --run "Implement the approved repository-wide migration"
```

With a catalog containing `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna`, `gpt-5.5`, and `gpt-5.2`, typical routing is: simple work -> Luna + none/low; everyday implementation -> Terra + medium; complex debugging/architecture -> Sol + high/xhigh; long-horizon professional work -> GPT-5.2 when it is available. The chosen effort is always clamped to the selected model's supported levels.

Workload can be inferred from the prompt or made explicit with `--workload simple|everyday|debugging|architecture|research|long_horizon|high_risk`. Explicit user model/effort constraints still take precedence.

If Codex's local model cache is incomplete, copy `rules/codex-models.example.json` to `~/.codex/autoroute.json` and keep only models and reasoning levels that are genuinely available in your environment.

AutoRoute does not live-probe every model on each request. For custom providers, verify a model with a minimal read-only `codex exec` probe before adding it to `~/.codex/autoroute.json`; an advertised model can still return a provider-side 503.

Availability commands:

```bash
python3 scripts/autoroute.py --list-models "refresh model inventory"
python3 scripts/autoroute.py --refresh-models --list-models "refresh model inventory"
python3 scripts/autoroute.py --ttl 0 "route this task"
```

The cache is refreshed on first use and then every 15 minutes by default. A Skill cannot receive a literal Codex process-start event, so this first-use/TTL refresh is the portable equivalent.

If you want an inventory check before every terminal Codex launch, use the bundled wrapper instead of `codex`. It re-discovers candidates every launch and live-probes only when the inventory changed or the TTL expired:

```bash
~/.codex/skills/autoroute/scripts/codex-with-autoroute
```

You may add your own shell alias to that wrapper. AutoRoute does not edit shell startup files automatically.

This repository root is the Skill directory. Clone it directly into the personal skills directory—there is no extra nested package layer:

```bash
git clone https://github.com/YOUR_ACCOUNT/AutoRoute.git ~/.codex/skills/autoroute
```

The required entrypoint is `SKILL.md`; UI metadata is in `agents/openai.yaml`.

## Validation

```bash
python3 "$HOME/.codex/skills/.system/skill-creator/scripts/quick_validate.py" .
python3 -m pytest
python3 tests/test_autoroute.py  # legacy standalone entrypoint
python3 scripts/run_evals.py --models-file tests/catalog.json
python3 scripts/benchmark_ab.py --output evals/results/ab-current.json
```

Routing policy is isolated in `scripts/router_policy.py` and can be tested
without starting subprocesses. The CLI and model discovery remain in
`scripts/autoroute.py`; keep new pure scoring rules in the policy module.

The router reads a local Codex model catalog when one is available. It also accepts any compatible catalog through `--models-file`, so the scoring and tests can be reused without publishing a user's private model list.

For a real control/treatment comparison, see [references/evaluation.md](references/evaluation.md). Codex JSON events expose per-turn usage fields; AutoRoute itself cannot guarantee a token saving because retries, latency, provider availability, and task success are part of the total cost.
