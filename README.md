# Codex Usage Reset

A small read-only macOS helper for checking Codex usage and banked reset expiry times.

It can run as:

- a native macOS menu-bar app with a small `C` icon
- a terminal checker
- a local browser dashboard
- an optional SwiftBar/xbar plugin

## What It Shows

- 5 hour Codex usage remaining
- weekly Codex usage remaining
- available reset count
- each available reset's expiry time
- each reset's time left

## Safety

This project reads your local Codex auth file at `~/.codex/auth.json` and calls read-only Codex backend endpoints:

```text
https://chatgpt.com/backend-api/wham/rate-limit-reset-credits
https://chatgpt.com/backend-api/wham/usage
```

It does not contain or call a reset redemption endpoint. Redeem resets only in the official Codex app.

These endpoints are not a public stable API. They can change or stop working.

## Requirements

- macOS 13 or newer for the native menu-bar app
- Codex Desktop signed in on the same Mac
- Python 3 at `/usr/bin/python3`
- Apple's Command Line Tools for the native app build

If `swiftc` is missing, install Command Line Tools:

```bash
xcode-select --install
```

Full Xcode is not required.

## Native Menu-Bar App

Build it:

```bash
./build-codex-menubar-app.command
```

Then open:

```text
Codex Usage.app
```

The app has no Dock icon and no main window. Look for a small `C` icon in the macOS menu bar. Click it to see usage and reset expiry details.

The default build targets the current Mac architecture. To try a universal build:

```bash
UNIVERSAL=1 ./build-codex-menubar-app.command
```

## Terminal Checker

```bash
/usr/bin/python3 codex-reset-expiry.py --pretty
```

Or double-click:

```text
check-codex-reset-expiry.command
```

## Browser Dashboard

```bash
/usr/bin/python3 codex-reset-expiry.py --serve --open
```

Or double-click:

```text
open-codex-reset-expiry-dashboard.command
```

This starts a read-only local server on `127.0.0.1`.

## Calendar Reminders

Generate an `.ics` file with reminders before reset expiry:

```bash
/usr/bin/python3 codex-reset-expiry.py --ics codex-reset-expiry.ics
```

The default reminders are 72, 24, and 6 hours before expiry.

## SwiftBar Or xbar

Install SwiftBar:

```bash
brew install --cask swiftbar
```

Then run:

```bash
./install-codex-menubar-widget.command
```

The plugin refreshes every 5 minutes.

## Tests

```bash
/usr/bin/python3 test_codex_reset_expiry.py
swiftc -typecheck CodexUsageMenuBar.swift
```

## Troubleshooting

If the menu-bar icon is running but not visible, your menu bar may be crowded or a menu-bar manager may have hidden it. Look for an item named `Codex Usage`.

If macOS blocks a downloaded `.command` file, run it from Terminal instead.
