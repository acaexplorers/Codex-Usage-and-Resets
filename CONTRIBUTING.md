# Contributing

Contributions are welcome, especially compatibility fixes for Codex rollout or usage-response changes.

## Ground Rules

- Keep all reset and usage operations read-only.
- Do not add reset redemption, credit consumption, or authentication export features.
- Do not retain prompt text, conversation content, tool output, task titles, or workspace paths in history data.
- Treat backend and rollout schemas as unstable and parse them defensively.
- Add or update tests for every behavioral change.

## Local Checks

Run:

```bash
make check
```

This executes:

- Python unit tests
- Swift type checking
- the reset-mutation safety scan

Build the app with:

```bash
./build-codex-menubar-app.command
```

## Pull Requests

Include:

- a concise description of the behavior change
- tests covering the change
- screenshots for visible UI changes
- confirmation that `make check` passes

Never include `~/.codex/auth.json`, a generated history cache, raw rollout files, or a real exported usage report in an issue or pull request.
