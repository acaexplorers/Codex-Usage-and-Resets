# Publishing

This project is best posted as a small GitHub repository rather than a gist because it has multiple scripts, a Swift source file, and tests.

## Suggested Repository Name

```text
codex-usage-reset
```

## Create A Public GitHub Repo

From this folder:

```bash
git init
git add .
git commit -m "Package Codex usage reset menu app"
gh repo create codex-usage-reset --public --source=. --remote=origin --push
```

Use `--private` instead of `--public` if you want to review it on GitHub first.

## Create A Source Zip

```bash
git archive --format=zip --output ../codex-usage-reset-source.zip HEAD
```

If you have not made a git commit yet:

```bash
ditto -c -k --sequesterRsrc --keepParent . ../codex-usage-reset-source.zip
```

## Short Post Draft

I made a small read-only macOS menu-bar helper for Codex usage resets.

It shows:

- 5 hour usage remaining
- weekly usage remaining
- available reset count
- each reset's expiry time
- time left before each reset expires

No Xcode required. It builds with Apple's Command Line Tools and uses the local Codex login already on your Mac.

Important: it is read-only. It does not redeem resets. Use the official Codex app for that.

## Longer Post Draft

I wrapped up a tiny local helper for Codex reset visibility on macOS.

The official app shows usage, but I wanted a clearer view of banked reset expiry dates so I would not accidentally let one expire. This gives me a small menu-bar `C` icon with 5 hour usage, weekly usage, available reset count, and each reset's expiry/time-left.

It is intentionally read-only:

- reads the local Codex auth file
- calls Codex usage/reset endpoints
- does not include a reset redemption endpoint
- does not print or store tokens

It can also run as a terminal checker, local browser dashboard, or SwiftBar plugin.

The backend endpoints are unofficial and may change, so treat this as a small personal utility rather than a stable API client.
