# Model Usage Metrics

The model report combines local Codex rollout records with the quota snapshots Codex stores after model responses. It is intended for personal observation and product feedback, not billing reconciliation.

## Data Sources

Codex rollout JSONL files provide:

- `turn_context.payload.model`: model active for the turn
- `turn_context.payload.effort`: reasoning-effort label when present
- `token_count.payload.info.last_token_usage`: token counters for the latest model response
- `token_count.payload.rate_limits`: five-hour and weekly quota snapshots

The collector reads only those records. It does not retain prompts, messages, tool output, task titles, workspace paths, or conversation text.

## Token Counters

For each model response, the report records:

- input tokens
- cached input tokens
- output tokens
- reasoning output tokens
- total tokens

Cached input is a subset of input tokens. Reasoning output is a subset of output tokens. They are shown as context, not added to the total a second time.

`Recorded tokens` is the sum of each response's reported total. If an older rollout omits that total, the collector falls back to input plus output.

## Local History Coverage

The `7d`, `30d`, and `90d` controls apply time cutoffs to rollout records that still exist under `~/.codex/sessions` and `~/.codex/archived_sessions`. `All` means all records available in those local folders; it does not mean the full lifetime of the Codex account.

The report displays the earliest and latest local timestamps it found. When fewer than 90 days remain locally, the `90d` and `All` views will intentionally match. Deleted, rotated, or unavailable rollout files cannot be reconstructed by this utility.

## Quota-Point Attribution

The five-hour and weekly percentages are account-wide snapshots. The report sorts all local token events by time and compares consecutive snapshots from the same quota window.

A positive increase is attributed to the model on the newer response:

```text
observed quota points = newer used percentage - previous used percentage
```

Window changes and percentage decreases are treated as resets and are not counted as consumption. This is intentionally conservative.

## Burn Rate

For each model:

```text
5h quota burn per 1M tokens =
  observed five-hour quota points / recorded tokens * 1,000,000
```

Lower values may indicate that a model consumes less displayed five-hour quota for the same number of recorded tokens. The comparison becomes more useful as `Total 5h quota points observed` grows.

## Important Limitations

1. Quota percentages are rounded, so individual one-point changes are coarse.
2. The quota is account-wide. Overlapping tasks, another Codex window, or another device can affect the next snapshot.
3. OpenAI does not publish the ChatGPT Codex quota-weighting formula. Different token types or models may be weighted differently.
4. The first observed snapshot in a quota window establishes a baseline. Consumption before that baseline is not attributed.
5. Deleted or unavailable rollout files cannot be included.
6. Model aliases and rollout formats may change.

For those reasons, the report says `observed` rather than `billed`, `charged`, or `exact quota cost`.

## Interpreting A Comparison

Before comparing two models, check:

- both have a meaningful quota-point sample
- model coverage is high
- cached-input shares are reasonably similar
- the models were used during comparable workflows
- overlapping tasks or other-device usage were limited

Do not draw a strong conclusion from a model with only a few observed quota points.

## JSON Export

The report export contains aggregate model statistics and a sampled quota timeline. It deliberately excludes authentication data and conversation content. Review it before sharing, because timestamps and model names still describe your usage pattern.
