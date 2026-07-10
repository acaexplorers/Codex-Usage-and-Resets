# Publishing

Canonical repository: [acaexplorers/Codex-Usage-and-Resets](https://github.com/acaexplorers/Codex-Usage-and-Resets)

## Release Checklist

1. Run `make check`.
2. Build and open `Codex Usage.app`.
3. Verify the menu-bar menu and HTML report at desktop and mobile widths.
4. Confirm all four history ranges switch in the static report.
5. Confirm JSON export contains no prompt, task, repository, or authentication data.
6. Update `CHANGELOG.md` and the app version in the build script.
7. Refresh `docs/images/usage-report.png` and `docs/images/menu-bar-menu.png` after visible changes.
8. Publish one coherent commit and tag the release.

## Suggested Release

```text
Tag: v0.2.1
Title: Five-hour and weekly quota cost by model
```

Release notes:

```markdown
## What's new

- Per-model token history from local Codex rollout records
- 7d, 30d, 90d, and all-history report views
- Range-aware five-hour quota trajectory
- Ranked 5-hour and weekly quota cost by model
- Readable `1 quota point every X tokens` estimates and sample sizes
- Cached-input, model-response, and sample-size context
- Sanitized JSON export
- Latest model and reasoning effort in the menu-bar menu
- Clearer reset expiry/countdown labels

The collector is local and read-only. It retains no prompt or conversation text, and reset redemption remains available only in the official Codex app.

The Codex backend and rollout formats are unofficial interfaces and may change.
```

## Announcement Draft

I built a read-only macOS menu-bar utility for Codex usage and resets.

It now tracks:

- 5-hour and weekly usage
- every reset credit and its expiry
- model-specific recorded token volume
- cached-input share
- ranked 5-hour and weekly quota cost by model
- readable tokens-per-quota-point estimates and sample sizes
- 7d / 30d / 90d / all-history trends

It can also export a sanitized JSON report for analysis or product feedback.

No full Xcode install required. It builds with Apple Command Line Tools and uses the Codex login already on your Mac.

Important caveat: quota percentages are rounded and account-wide, so model comparisons are observed estimates, not billing data.

https://github.com/acaexplorers/Codex-Usage-and-Resets

## Short Announcement

Made a read-only Codex usage monitor for macOS: reset expiries, 5h/weekly limits, model token history, cached-input share, quota trends, and JSON export from one small menu-bar icon.

No full Xcode required. No reset redemption. Local history retains no prompt text.

https://github.com/acaexplorers/Codex-Usage-and-Resets
