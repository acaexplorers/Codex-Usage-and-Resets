#!/usr/bin/env python3
"""
SwiftBar/xbar menu-bar plugin for Codex usage and reset expiry.

Keep this file next to codex-reset-expiry.py, or set CODEX_RESET_HELPER to the
helper script path. Refresh interval is controlled by the filename: 5m.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


HERE = Path(__file__).resolve().parent
DEFAULT_HELPER = HERE / "codex-reset-expiry.py"
HELPER_PATH = Path(os.environ.get("CODEX_RESET_HELPER", DEFAULT_HELPER)).expanduser()
DASHBOARD_LAUNCHER = HERE / "open-codex-reset-expiry-dashboard.command"


def load_helper():
    if not HELPER_PATH.exists():
        raise FileNotFoundError(f"Helper not found: {HELPER_PATH}")
    spec = importlib.util.spec_from_file_location("codex_reset_expiry", HELPER_PATH)
    module = importlib.util.module_from_spec(spec)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load helper: {HELPER_PATH}")
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def color_for_remaining(percent: int) -> str:
    if percent <= 10:
        return "#B33636"
    if percent <= 25:
        return "#9B6A00"
    return "#217A4B"


def escape(value: str) -> str:
    return value.replace("|", "/").replace("\n", " ")


def print_error(error: Exception) -> None:
    print("Codex ? | color=#9B6A00")
    print("---")
    print("Codex usage unavailable | color=#B33636")
    print(f"{escape(str(error))} | color=#68716D")
    print("Install helper next to this plugin or set CODEX_RESET_HELPER. | color=#68716D")


def main() -> int:
    try:
        helper = load_helper()
        reset_payload = helper.fetch_reset_payload(helper.default_auth_path())
        usage_payload = helper.try_fetch_usage_payload(helper.default_auth_path())
        now = datetime.now(timezone.utc)
        credits = helper.parse_credits(reset_payload)
        windows = helper.parse_usage_windows(usage_payload)
        available = helper.count_available_credits(reset_payload, credits)
    except Exception as error:
        print_error(error)
        return 0

    by_label = {window.label: window for window in windows}
    five_hour = by_label.get("5 hour")
    weekly = by_label.get("Weekly")

    title_parts = []
    if five_hour:
        title_parts.append(f"5h{five_hour.remaining_percent}")
    if weekly:
        title_parts.append(f"W{weekly.remaining_percent}")
    if available:
        title_parts.append(f"R {available}")
    title = "Codex " + " ".join(title_parts) if title_parts else "Codex"

    title_color = "#217A4B"
    low_values = [window.remaining_percent for window in (five_hour, weekly) if window]
    if low_values and min(low_values) <= 10:
        title_color = "#B33636"
    elif low_values and min(low_values) <= 25:
        title_color = "#9B6A00"

    print(f"{title} | color={title_color}")
    print("---")
    print("Codex usage | size=14")
    if windows:
        for window in windows:
            print(
                f"{window.label}: {window.remaining_percent}% left | "
                f"color={color_for_remaining(window.remaining_percent)}"
            )
            print(
                f"Resets {helper.format_reset_at(window.reset_at, now)} | color=#68716D"
            )
    else:
        print("Usage windows unavailable | color=#9B6A00")

    print("---")
    noun = "reset" if available == 1 else "resets"
    print(f"{available} available {noun}")
    available_credits = helper.available_credits_with_expiry(credits)
    if available_credits:
        for index, credit in enumerate(available_credits, start=1):
            seconds_left = helper.credit_seconds_left(credit, now) or 0
            print(f"Reset {index}: {helper.friendly_title(credit)}")
            print(f"Expires {helper.format_dt(credit.expires_at)}")
            print(
                f"Time left {helper.format_duration_words(seconds_left)} | color=#68716D"
            )
    else:
        print("No available reset expiry returned | color=#68716D")

    print("---")
    if DASHBOARD_LAUNCHER.exists():
        print(
            "Open live dashboard | "
            f"bash={DASHBOARD_LAUNCHER} terminal=true refresh=true"
        )
    print("Refresh | refresh=true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
