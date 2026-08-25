# Evaluation

Measure routing as a quality/cost tradeoff, not token reduction alone.

Run the same task set in two randomized arms:

1. **Control:** current Codex model and reasoning settings, with AutoRoute disabled.
2. **Treatment:** AutoRoute recommendation, with the selected model and effort passed to a new `codex exec` session.

Keep prompts, repository snapshot, tool permissions, and retry policy identical. Use at least 20 tasks per class (simple change, normal implementation, debugging, repository-wide design, and high-risk migration) before drawing conclusions. Record one JSON line per run from `codex exec --json`; the `turn.completed` event contains `usage.input_tokens`, `cached_input_tokens`, `output_tokens`, and `reasoning_output_tokens`.

Required metrics:

- success rate and evaluator score;
- total tokens = input + output (track cached input separately);
- reasoning tokens and wall-clock latency;
- retry count, tool failures, and provider/model availability failures;
- cost, if the provider exposes a price sheet for the exact account/model.

Report medians and bootstrap confidence intervals by task class. A useful primary comparison is:

```text
quality-adjusted cost = total cost / successful-task score
```

Do not claim a token saving from one task. A route that saves input tokens but causes a 503, retry, or lower-quality patch is not cheaper in practice. Model catalogs can be stale; treat unavailable-model errors as a routing failure and include them in the treatment arm's failure rate.
