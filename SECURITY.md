# Security

This helper is intentionally read-only.

It reads `~/.codex/auth.json` to use the same local Codex login as the Codex Desktop app. It does not store tokens, print tokens, send tokens anywhere except the Codex backend endpoints it calls, or include a reset redemption endpoint.

Before publishing or changing this project, run:

```bash
rg -n "rate-limit-reset-credits/consume|redeem_request_id|\\.post\\(|access_token|refresh_token|id_token" .
```

Expected matches:

- `access_token` inside `codex-reset-expiry.py`, where the token is read from the local Codex auth file
- `access_token` in the test that verifies UI payloads do not expose tokens

There should be no `consume` endpoint, no `redeem_request_id`, and no HTTP POST reset call.

These Codex backend endpoints are not a public stable API and may change.
