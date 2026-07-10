# Security

## Security Model

This utility is intentionally read-only.

It reads `~/.codex/auth.json` to use the same local Codex login as Codex Desktop. The token is used only in `Authorization` headers sent to the two configured `chatgpt.com` read endpoints. It is not printed, copied into reports, or written to the history cache.

The model-history collector reads local rollout JSONL files. It retains only:

- event timestamps
- model and reasoning-effort labels
- input, cached-input, output, reasoning, and total token counters
- five-hour and weekly quota snapshots

It does not retain prompts, messages, conversation content, tool output, task titles, workspace paths, or repository names.

The incremental cache is `~/.codex/codex-usage-reset-history-v1.json.gz`. It is written atomically with mode `0600`.

## Network Access

The utility calls:

```text
GET https://chatgpt.com/backend-api/wham/rate-limit-reset-credits
GET https://chatgpt.com/backend-api/wham/usage
```

These are not public stable APIs and may change.

There is no HTTP POST request and no reset-credit consumption or redemption path. Reset actions belong in the official Codex app.

## Exports

The model JSON export is sanitized and contains aggregate statistics plus sampled timestamps/model labels for the quota chart. It contains no authentication data or conversation text.

Usage timestamps and model names can still reveal behavioral patterns. Review an export before sharing it publicly.

## Verification

Run:

```bash
make safety
```

The safety target scans executable sources for reset-consumption paths, redemption request IDs, and HTTP POST calls.

Also run the full test suite:

```bash
make check
```

Tests verify that synthetic prompt and message text never enters collected history events.

## Reporting A Vulnerability

Open a private security advisory in the GitHub repository when possible. Do not include Codex authentication files, access tokens, raw rollout files, or real conversation content in a report.
