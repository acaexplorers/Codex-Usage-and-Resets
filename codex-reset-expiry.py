#!/usr/bin/env python3
"""
Read-only Codex reset expiry checker.

This uses your existing local Codex login to show when banked reset credits
expire. It does not redeem anything.
"""

from __future__ import annotations

import argparse
import html
import http.server
import json
import os
import shutil
import socketserver
import sys
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


RESET_CREDITS_URL = "https://chatgpt.com/backend-api/wham/rate-limit-reset-credits"
USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
RESET_CREDITS_HOST = "chatgpt.com"


class Style:
    def __init__(self, enabled: bool):
        self.enabled = enabled

    def text(self, value: str, code: str) -> str:
        if not self.enabled:
            return value
        return f"\033[{code}m{value}\033[0m"

    def bold(self, value: str) -> str:
        return self.text(value, "1")

    def dim(self, value: str) -> str:
        return self.text(value, "2")

    def green(self, value: str) -> str:
        return self.text(value, "32")

    def yellow(self, value: str) -> str:
        return self.text(value, "33")

    def red(self, value: str) -> str:
        return self.text(value, "31")

    def cyan(self, value: str) -> str:
        return self.text(value, "36")


@dataclass(frozen=True)
class ResetCredit:
    title: str
    status: str
    granted_at: datetime | None
    expires_at: datetime | None


@dataclass(frozen=True)
class UsageWindow:
    label: str
    used_percent: int
    remaining_percent: int
    window_seconds: int | None
    reset_at: datetime | None
    reset_after_seconds: int | None


def default_auth_path() -> Path:
    codex_home = os.environ.get("CODEX_HOME")
    if codex_home:
        return Path(codex_home).expanduser() / "auth.json"
    return Path("~/.codex/auth.json").expanduser()


def load_auth(path: Path) -> tuple[str, str]:
    auth = json.loads(path.expanduser().read_text())
    token_bucket = auth.get("tokens") if isinstance(auth.get("tokens"), dict) else auth
    token = token_bucket.get("access_token") or auth.get("access_token")
    account = (
        token_bucket.get("account_id")
        or auth.get("account_id")
        or auth.get("chatgpt_account_id")
    )
    if not token:
        raise ValueError(f"{path} does not contain an access token")
    if not account:
        raise ValueError(f"{path} does not contain a ChatGPT account id")
    return token, account


def auth_headers(auth_path: Path) -> dict[str, str]:
    token, account = load_auth(auth_path)
    return {
        "Authorization": f"Bearer {token}",
        "ChatGPT-Account-ID": account,
        "OpenAI-Beta": "codex-1",
        "originator": "Codex Desktop",
    }


def fetch_json_url(auth_path: Path, url: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers=auth_headers(auth_path),
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_reset_payload(auth_path: Path) -> dict[str, Any]:
    return fetch_json_url(auth_path, RESET_CREDITS_URL)


def fetch_usage_payload(auth_path: Path) -> dict[str, Any]:
    return fetch_json_url(auth_path, USAGE_URL)


def try_fetch_usage_payload(auth_path: Path) -> dict[str, Any] | None:
    try:
        return fetch_usage_payload(auth_path)
    except Exception:
        return None


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def parse_credits(payload: dict[str, Any]) -> list[ResetCredit]:
    credits = []
    for item in payload.get("credits") or []:
        credits.append(
            ResetCredit(
                title=item.get("title") or "Codex rate-limit reset",
                status=item.get("status") or "unknown",
                granted_at=parse_datetime(item.get("granted_at")),
                expires_at=parse_datetime(item.get("expires_at")),
            )
        )
    return sorted(credits, key=lambda credit: credit.expires_at or datetime.max.replace(tzinfo=timezone.utc))


def clamp_percent(value: int) -> int:
    return max(0, min(100, int(value)))


def parse_unix_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(int(value), timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def usage_label_for_seconds(seconds: int | None, fallback: str) -> str:
    if seconds == 18_000:
        return "5 hour"
    if seconds == 604_800:
        return "Weekly"
    if seconds is None:
        return fallback
    hours = seconds / 3600
    if hours < 48:
        if hours.is_integer():
            return f"{int(hours)} hour"
        return f"{hours:.1f} hour"
    days = seconds / 86_400
    if days.is_integer():
        return f"{int(days)} day"
    return f"{days:.1f} day"


def parse_usage_window(raw: dict[str, Any] | None, fallback: str) -> UsageWindow | None:
    if not raw:
        return None
    window_seconds = raw.get("limit_window_seconds")
    used = clamp_percent(raw.get("used_percent", 0))
    return UsageWindow(
        label=usage_label_for_seconds(window_seconds, fallback),
        used_percent=used,
        remaining_percent=clamp_percent(100 - used),
        window_seconds=window_seconds,
        reset_at=parse_unix_timestamp(raw.get("reset_at")),
        reset_after_seconds=raw.get("reset_after_seconds"),
    )


def parse_usage_windows(payload: dict[str, Any] | None) -> list[UsageWindow]:
    if not payload:
        return []
    rate_limit = payload.get("rate_limit") or {}
    windows = [
        parse_usage_window(rate_limit.get("primary_window"), "Primary"),
        parse_usage_window(rate_limit.get("secondary_window"), "Secondary"),
    ]
    return [window for window in windows if window is not None]


def format_dt(value: datetime | None, local_tz: timezone | None = None) -> str:
    if value is None:
        return "unknown"
    local_value = value.astimezone(local_tz)
    return local_value.strftime("%b %-d, %Y at %-I:%M %p %Z")


def format_dt_compact(value: datetime | None, local_tz: timezone | None = None) -> str:
    if value is None:
        return "unknown"
    local_value = value.astimezone(local_tz)
    return local_value.strftime("%b %-d, %Y at %-I:%M %p")


def format_reset_at(
    value: datetime | None,
    now: datetime,
    local_tz: timezone | None = None,
) -> str:
    if value is None:
        return "unknown"
    local_value = value.astimezone(local_tz)
    local_now = now.astimezone(local_tz)
    if local_value.date() == local_now.date():
        return local_value.strftime("%-I:%M %p %Z")
    return local_value.strftime("%b %-d at %-I:%M %p %Z")


def format_reset_at_compact(
    value: datetime | None,
    now: datetime,
    local_tz: timezone | None = None,
) -> str:
    if value is None:
        return "unknown"
    local_value = value.astimezone(local_tz)
    local_now = now.astimezone(local_tz)
    if local_value.date() == local_now.date():
        return local_value.strftime("%-I:%M %p")
    return local_value.strftime("%b %-d at %-I:%M %p")


def format_duration(seconds: float) -> str:
    if seconds <= 0:
        return "expired"
    minutes = int(seconds // 60)
    days, rem_minutes = divmod(minutes, 60 * 24)
    hours, mins = divmod(rem_minutes, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if mins and not days:
        parts.append(f"{mins}m")
    return " ".join(parts) or "under 1m"


def format_duration_words(seconds: float) -> str:
    if seconds <= 0:
        return "expired"
    minutes = int(seconds // 60)
    days, rem_minutes = divmod(minutes, 60 * 24)
    hours, mins = divmod(rem_minutes, 60)
    parts = []
    if days:
        parts.append(f"{days} {'day' if days == 1 else 'days'}")
    if hours:
        parts.append(f"{hours} {'hour' if hours == 1 else 'hours'}")
    if mins and not days:
        parts.append(f"{mins} {'minute' if mins == 1 else 'minutes'}")
    return ", ".join(parts) or "under 1 minute"


def count_available_credits(payload: dict[str, Any], credits: list[ResetCredit]) -> int:
    available_count = payload.get("available_count")
    if available_count is None:
        return sum(1 for credit in credits if credit.status == "available")
    return int(available_count)


def friendly_title(credit: ResetCredit) -> str:
    if credit.title.lower() == "one free rate limit reset":
        return "Free Codex reset"
    return credit.title


def credit_seconds_left(credit: ResetCredit, now: datetime) -> float | None:
    if credit.expires_at is None:
        return None
    return (credit.expires_at - now).total_seconds()


def available_credits_with_expiry(credits: list[ResetCredit]) -> list[ResetCredit]:
    return [
        credit
        for credit in credits
        if credit.status == "available" and credit.expires_at is not None
    ]


def lifetime_remaining_percent(credit: ResetCredit, now: datetime) -> int | None:
    if credit.granted_at is None or credit.expires_at is None:
        return None
    total = (credit.expires_at - credit.granted_at).total_seconds()
    if total <= 0:
        return 0
    left = (credit.expires_at - now).total_seconds()
    return max(0, min(100, round((left / total) * 100)))


def ascii_bar(percent: int | None, width: int = 18) -> str:
    if percent is None:
        return "[" + ("?" * width) + "]"
    filled = round((max(0, min(100, percent)) / 100) * width)
    return "[" + ("#" * filled) + ("-" * (width - filled)) + "]"


def render_report(
    payload: dict[str, Any],
    usage_payload: dict[str, Any] | None = None,
    now: datetime | None = None,
    local_tz: timezone | None = None,
    warning_hours: float = 48,
) -> str:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    credits = parse_credits(payload)
    available_count = count_available_credits(payload, credits)
    usage_windows = parse_usage_windows(usage_payload)

    lines = [
        "Codex reset credits",
        f"Checked: {format_dt(now, local_tz)}",
        f"Available resets: {available_count}",
        "",
        "Safety: read-only expiry check; redeem resets only in the Codex app.",
    ]

    if not credits:
        lines.extend(["", "No reset-credit details were returned for this account."])
        return "\n".join(lines)

    if usage_windows:
        lines.append("")
        lines.append("Usage")
        for window in usage_windows:
            lines.append(
                f"   {window.label}: {window.remaining_percent}% left "
                f"({window.used_percent}% used), resets {format_reset_at(window.reset_at, now, local_tz)}"
            )

    lines.append("")
    for index, credit in enumerate(credits, start=1):
        time_left = (
            format_duration((credit.expires_at - now).total_seconds())
            if credit.expires_at
            else "unknown"
        )
        urgent = (
            credit.expires_at is not None
            and 0 <= (credit.expires_at - now).total_seconds() <= warning_hours * 3600
            and credit.status == "available"
        )
        marker = " ! " if urgent else " - "
        lines.extend(
            [
                f"Reset {index}: {credit.title}",
                f"{marker}Status: {credit.status}",
                f"   Granted: {format_dt(credit.granted_at, local_tz)}",
                f"   Expires: {format_dt(credit.expires_at, local_tz)}",
                f"   Time left: {time_left}",
            ]
        )
        if index != len(credits):
            lines.append("")
    return "\n".join(lines)


def visible_len(value: str) -> int:
    length = 0
    in_escape = False
    for char in value:
        if char == "\033":
            in_escape = True
            continue
        if in_escape:
            if char == "m":
                in_escape = False
            continue
        length += 1
    return length


def pad_visible(value: str, width: int) -> str:
    return value + (" " * max(0, width - visible_len(value)))


def box(lines: list[str], width: int = 62) -> str:
    usable = max(20, width - 4)
    border = "+" + "-" * (usable + 2) + "+"
    rendered = [border]
    for line in lines:
        rendered.append("| " + pad_visible(line[:usable], usable) + " |")
    rendered.append(border)
    return "\n".join(rendered)


def render_pretty_report(
    payload: dict[str, Any],
    usage_payload: dict[str, Any] | None = None,
    now: datetime | None = None,
    local_tz: timezone | None = None,
    warning_hours: float = 48,
    color: bool = True,
) -> str:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    style = Style(color)
    credits = parse_credits(payload)
    usage_windows = parse_usage_windows(usage_payload)
    available_count = count_available_credits(payload, credits)
    terminal_width = shutil.get_terminal_size((88, 24)).columns
    width = min(76, max(56, terminal_width - 8))

    noun = "reset" if available_count == 1 else "resets"
    if available_count > 0:
        status_line = style.green(f"{available_count} available {noun}")
    else:
        status_line = style.yellow("No available resets")

    lines = [
        style.bold("Codex reset expiry"),
        f"Checked  {style.dim(format_dt(now, local_tz))}",
        f"Status   {status_line}",
        "",
        style.dim("Read-only check. Redeem only inside the Codex app."),
    ]

    if not credits:
        lines.extend(["", style.yellow("No reset-credit details were returned.")])
        return box(lines, width)

    if usage_windows:
        lines.extend(["", style.bold("Usage remaining")])
        for window in usage_windows:
            percent = window.remaining_percent
            if percent <= 10:
                percent_label = style.red(f"{percent}% left")
            elif percent <= 25:
                percent_label = style.yellow(f"{percent}% left")
            else:
                percent_label = style.green(f"{percent}% left")
            lines.append(
                f"{window.label:<8} {ascii_bar(percent, width=14)} "
                f"{percent_label}  resets {format_reset_at(window.reset_at, now, local_tz)}"
            )

    available_with_expiry = available_credits_with_expiry(credits)
    if available_with_expiry:
        next_credit = available_with_expiry[0]
        seconds_left = credit_seconds_left(next_credit, now)
        lines.extend(
            [
                "",
                style.bold("Next expiry"),
                f"Local    {style.cyan(format_dt(next_credit.expires_at, local_tz))}",
                f"UTC      {format_dt(next_credit.expires_at, timezone.utc)}",
                f"Left     {style.green(format_duration_words(seconds_left or 0))}",
            ]
        )

    for index, credit in enumerate(credits, start=1):
        seconds_left = credit_seconds_left(credit, now)
        time_left = format_duration(seconds_left) if seconds_left is not None else "unknown"
        percent_left = lifetime_remaining_percent(credit, now)
        urgent = (
            seconds_left is not None
            and 0 <= seconds_left <= warning_hours * 3600
            and credit.status == "available"
        )
        expired = seconds_left is not None and seconds_left <= 0
        if expired:
            time_left_label = style.red(time_left)
        elif urgent:
            time_left_label = style.yellow(time_left)
        else:
            time_left_label = style.green(time_left)

        lines.extend(
            [
                "",
                style.bold(f"Reset {index}: {friendly_title(credit)}"),
                f"Expires  {style.cyan(format_dt(credit.expires_at, local_tz))}",
                f"UTC      {format_dt(credit.expires_at, timezone.utc)}",
                f"Left     {time_left_label}",
                f"Life     {ascii_bar(percent_left)} {percent_left if percent_left is not None else '?'}%",
                f"Granted  {style.dim(format_dt(credit.granted_at, local_tz))}",
                f"State    {credit.status}",
            ]
        )

    return box(lines, width)


def html_escape(value: str) -> str:
    return html.escape(value, quote=True)


def html_status_class(credit: ResetCredit, now: datetime, warning_hours: float) -> str:
    seconds_left = credit_seconds_left(credit, now)
    if seconds_left is None:
        return "unknown"
    if seconds_left <= 0:
        return "expired"
    if seconds_left <= warning_hours * 3600:
        return "soon"
    return "safe"


def render_html_dashboard(
    payload: dict[str, Any],
    usage_payload: dict[str, Any] | None = None,
    now: datetime | None = None,
    local_tz: timezone | None = None,
    warning_hours: float = 48,
    live: bool = False,
    refresh_seconds: int | None = None,
) -> str:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    credits = parse_credits(payload)
    usage_windows = parse_usage_windows(usage_payload)
    available_count = count_available_credits(payload, credits)
    available_with_expiry = available_credits_with_expiry(credits)
    next_credit = available_with_expiry[0] if available_with_expiry else None
    next_left = (
        format_duration_words(credit_seconds_left(next_credit, now) or 0)
        if next_credit
        else "No available reset expiry"
    )
    next_expiry = (
        format_dt(next_credit.expires_at, local_tz)
        if next_credit
        else "No available resets"
    )
    noun = "reset" if available_count == 1 else "resets"

    cards = []
    for index, credit in enumerate(credits, start=1):
        seconds_left = credit_seconds_left(credit, now)
        percent_left = lifetime_remaining_percent(credit, now)
        status_class = html_status_class(credit, now, warning_hours)
        cards.append(
            f"""
            <section class="reset-card {status_class}">
              <div class="reset-head">
                <p class="eyebrow">Reset {index}</p>
                <span class="pill">{html_escape(credit.status)}</span>
              </div>
              <h2>{html_escape(friendly_title(credit))}</h2>
              <p class="big-date">{html_escape(format_dt(credit.expires_at, local_tz))}</p>
              <p class="subtle">UTC: {html_escape(format_dt(credit.expires_at, timezone.utc))}</p>
              <div class="bar" aria-label="Reset lifetime remaining">
                <span style="width: {0 if percent_left is None else percent_left}%"></span>
              </div>
              <dl>
                <div><dt>Time left</dt><dd>{html_escape(format_duration_words(seconds_left or 0) if seconds_left is not None else "unknown")}</dd></div>
                <div><dt>Lifetime left</dt><dd>{html_escape(str(percent_left) + "%" if percent_left is not None else "unknown")}</dd></div>
                <div><dt>Granted</dt><dd>{html_escape(format_dt(credit.granted_at, local_tz))}</dd></div>
              </dl>
            </section>
            """
        )

    usage_cards = []
    for window in usage_windows:
        class_name = "safe"
        if window.remaining_percent <= 10:
            class_name = "expired"
        elif window.remaining_percent <= 25:
            class_name = "soon"
        usage_cards.append(
            f"""
            <section class="usage-card {class_name}">
              <div>
                <p class="eyebrow">{html_escape(window.label)} usage limit</p>
                <p class="usage-percent">{window.remaining_percent}% left</p>
                <p class="subtle">{window.used_percent}% used</p>
              </div>
              <div class="bar" aria-label="{html_escape(window.label)} usage remaining">
                <span style="width: {window.remaining_percent}%"></span>
              </div>
              <p class="subtle">Resets {html_escape(format_reset_at(window.reset_at, now, local_tz))}</p>
            </section>
            """
        )

    if not cards:
        cards.append(
            """
            <section class="reset-card unknown">
              <h2>No reset details returned</h2>
              <p class="subtle">The endpoint did not return per-credit expiry information for this account.</p>
            </section>
            """
        )

    refresh_meta = (
        f'<meta http-equiv="refresh" content="{int(refresh_seconds)}">'
        if refresh_seconds and refresh_seconds > 0
        else ""
    )
    refresh_copy = (
        f"Auto-refreshes every {refresh_seconds} seconds while this local server is running."
        if live and refresh_seconds and refresh_seconds > 0
        else "Refresh the page to fetch a new read-only snapshot."
    )
    mode_label = "Live local dashboard" if live else "Local snapshot"

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  {refresh_meta}
  <title>Codex Reset Expiry</title>
  <style>
    :root {{
      color-scheme: light dark;
      --bg: #f6f7f2;
      --panel: #ffffff;
      --ink: #1d2220;
      --muted: #68716d;
      --line: #dfe4df;
      --green: #217a4b;
      --yellow: #9b6a00;
      --red: #b33636;
      --accent: #256c86;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    @media (prefers-color-scheme: dark) {{
      :root {{
        --bg: #111513;
        --panel: #1b211f;
        --ink: #eef2ee;
        --muted: #a8b1ad;
        --line: #303a36;
        --accent: #69b4ce;
      }}
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      min-height: 100vh;
      padding: 32px;
    }}
    main {{
      max-width: 920px;
      margin: 0 auto;
    }}
    header {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 18px;
      align-items: end;
      margin-bottom: 20px;
    }}
    h1 {{
      font-size: 28px;
      line-height: 1.1;
      margin: 0 0 8px;
      letter-spacing: 0;
    }}
    p {{ margin: 0; }}
    .checked {{ color: var(--muted); font-size: 14px; }}
    .toolbar {{
      display: flex;
      gap: 10px;
      align-items: center;
      justify-content: flex-end;
      flex-wrap: wrap;
    }}
    .mode {{
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 6px 11px;
      color: var(--muted);
      font-size: 13px;
      background: var(--panel);
    }}
    .button {{
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 8px 12px;
      color: var(--ink);
      text-decoration: none;
      background: var(--panel);
      font-weight: 650;
    }}
    .button:hover {{
      border-color: color-mix(in srgb, var(--accent), var(--line) 30%);
    }}
    .summary {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 12px;
      margin-bottom: 14px;
    }}
    .summary-card, .reset-card, .usage-card, .note {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 18px;
    }}
    .summary-card .label {{
      color: var(--muted);
      font-size: 13px;
      margin-bottom: 7px;
    }}
    .summary-card .value {{
      font-size: 22px;
      font-weight: 700;
    }}
    .resets {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
      gap: 14px;
      margin-top: 14px;
    }}
    .usage-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 14px;
      margin-top: 14px;
    }}
    .usage-card {{
      display: grid;
      gap: 12px;
    }}
    .usage-percent {{
      font-size: 28px;
      line-height: 1;
      font-weight: 800;
    }}
    .reset-head {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      margin-bottom: 12px;
    }}
    .eyebrow {{
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
      letter-spacing: .08em;
      text-transform: uppercase;
    }}
    .pill {{
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 4px 9px;
      font-size: 12px;
      color: var(--muted);
    }}
    .reset-card h2 {{
      font-size: 19px;
      margin: 0 0 10px;
      letter-spacing: 0;
    }}
    .big-date {{
      font-size: 22px;
      font-weight: 750;
      line-height: 1.2;
    }}
    .subtle {{
      color: var(--muted);
      margin-top: 6px;
      font-size: 14px;
    }}
    .bar {{
      width: 100%;
      height: 10px;
      background: color-mix(in srgb, var(--line), transparent 15%);
      border-radius: 999px;
      overflow: hidden;
      margin: 18px 0 12px;
    }}
    .bar span {{
      display: block;
      height: 100%;
      border-radius: inherit;
      background: var(--green);
    }}
    .soon .bar span {{ background: var(--yellow); }}
    .expired .bar span {{ background: var(--red); }}
    dl {{
      display: grid;
      gap: 9px;
      margin: 0;
    }}
    dl div {{
      display: flex;
      justify-content: space-between;
      gap: 18px;
      border-top: 1px solid var(--line);
      padding-top: 9px;
    }}
    dt {{ color: var(--muted); }}
    dd {{ margin: 0; font-weight: 650; text-align: right; }}
    .note {{
      color: var(--muted);
      margin-top: 14px;
      font-size: 14px;
    }}
    .note strong {{ color: var(--ink); }}
    @media (max-width: 720px) {{
      body {{ padding: 20px; }}
      header, .summary, .usage-grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>Codex reset expiry</h1>
        <p class="checked">Snapshot generated {html_escape(format_dt(now, local_tz))}</p>
      </div>
      <div class="toolbar">
        <span class="mode">{html_escape(mode_label)}</span>
        <a class="button" href="/">Refresh</a>
      </div>
    </header>

    <section class="summary">
      <div class="summary-card">
        <p class="label">Available</p>
        <p class="value">{available_count} {html_escape(noun)}</p>
      </div>
      <div class="summary-card">
        <p class="label">Next expiry</p>
        <p class="value">{html_escape(next_expiry)}</p>
      </div>
      <div class="summary-card">
        <p class="label">Time left</p>
        <p class="value">{html_escape(next_left)}</p>
      </div>
    </section>

    <section class="usage-grid">
      {"".join(usage_cards)}
    </section>

    <section class="resets">
      {"".join(cards)}
    </section>

    <p class="note"><strong>Safety:</strong> this page uses a local read-only checker bound to your machine. Redeem resets only inside the Codex app. {html_escape(refresh_copy)}</p>
  </main>
</body>
</html>
"""


def build_summary_payload(
    reset_payload: dict[str, Any],
    usage_payload: dict[str, Any] | None = None,
    now: datetime | None = None,
    local_tz: timezone | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    credits = parse_credits(reset_payload)
    usage_windows = parse_usage_windows(usage_payload)
    available_count = count_available_credits(reset_payload, credits)
    available_with_expiry = available_credits_with_expiry(credits)
    next_credit = available_with_expiry[0] if available_with_expiry else None
    seconds_left = credit_seconds_left(next_credit, now) if next_credit else None
    reset_items = []
    for index, credit in enumerate(available_with_expiry, start=1):
        item_seconds_left = credit_seconds_left(credit, now)
        reset_items.append(
            {
                "number": index,
                "title": friendly_title(credit),
                "status": credit.status,
                "expires": format_dt_compact(credit.expires_at, local_tz),
                "expiresUTC": format_dt(credit.expires_at, timezone.utc),
                "timeLeft": (
                    format_duration_words(item_seconds_left)
                    if item_seconds_left is not None
                    else None
                ),
            }
        )

    return {
        "checkedAt": format_dt_compact(now, local_tz),
        "usage": [
            {
                "label": window.label,
                "remainingPercent": window.remaining_percent,
                "usedPercent": window.used_percent,
                "resetsAt": format_reset_at_compact(window.reset_at, now, local_tz),
            }
            for window in usage_windows
        ],
        "resets": {
            "availableCount": available_count,
            "nextExpiry": (
                format_dt_compact(next_credit.expires_at, local_tz) if next_credit else None
            ),
            "nextExpiryUTC": (
                format_dt(next_credit.expires_at, timezone.utc) if next_credit else None
            ),
            "nextTimeLeft": (
                format_duration_words(seconds_left) if seconds_left is not None else None
            ),
            "items": reset_items,
        },
        "safety": "Read-only. Redeem resets only inside the Codex app.",
    }


def render_error_html(message: str) -> str:
    escaped = html_escape(message)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Codex Reset Expiry Error</title>
  <style>
    body {{
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      background: #f6f7f2;
      color: #1d2220;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      padding: 24px;
    }}
    main {{
      max-width: 680px;
      border: 1px solid #dfe4df;
      border-radius: 8px;
      background: white;
      padding: 24px;
    }}
    h1 {{ margin: 0 0 10px; font-size: 24px; }}
    p {{ margin: 0; color: #68716d; line-height: 1.5; }}
  </style>
</head>
<body>
  <main>
    <h1>Could not load Codex reset expiry</h1>
    <p>{escaped}</p>
  </main>
</body>
</html>
"""


def ics_escape(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def datetime_to_ics(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def build_ics(payload: dict[str, Any], remind_hours: list[int]) -> str:
    credits = [
        credit
        for credit in parse_credits(payload)
        if credit.status == "available" and credit.expires_at is not None
    ]
    now_stamp = datetime_to_ics(datetime.now(timezone.utc))
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Codex Reset Expiry//Read Only Checker//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
    ]
    for index, credit in enumerate(credits, start=1):
        expires = credit.expires_at or datetime.now(timezone.utc)
        uid = f"codex-reset-{index}-{datetime_to_ics(expires)}@local"
        lines.extend(
            [
                "BEGIN:VEVENT",
                f"UID:{uid}",
                f"DTSTAMP:{now_stamp}",
                f"DTSTART:{datetime_to_ics(expires)}",
                f"DTEND:{datetime_to_ics(expires)}",
                f"SUMMARY:{ics_escape('Codex reset expires')}",
                f"DESCRIPTION:{ics_escape(credit.title + ' expires. Redeem only from the Codex app if you need it.')}",
            ]
        )
        for hours in remind_hours:
            lines.extend(
                [
                    "BEGIN:VALARM",
                    "ACTION:DISPLAY",
                    f"DESCRIPTION:{ics_escape(f'Codex reset expires in {hours} hours')}",
                    f"TRIGGER:-PT{hours}H",
                    "END:VALARM",
                ]
            )
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def parse_hours(value: str) -> list[int]:
    if not value.strip():
        return []
    hours = []
    for part in value.split(","):
        number = int(part.strip())
        if number <= 0:
            raise argparse.ArgumentTypeError("reminder hours must be positive")
        hours.append(number)
    return hours


class ReusableThreadingTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def serve_dashboard(
    auth_path: Path,
    port: int,
    warning_hours: float,
    refresh_seconds: int,
    open_browser: bool,
) -> int:
    class DashboardHandler(http.server.BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return

        def send_text(self, status: int, body: str) -> None:
            body_bytes = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body_bytes)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body_bytes)

        def send_html(self, status: int, body: str) -> None:
            body_bytes = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body_bytes)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body_bytes)

        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == "/health":
                self.send_text(200, "ok\n")
                return
            if parsed.path == "/favicon.ico":
                self.send_response(204)
                self.end_headers()
                return
            if parsed.path not in {"/", "/index.html"}:
                self.send_text(404, "not found\n")
                return

            try:
                payload = fetch_reset_payload(auth_path)
                usage_payload = try_fetch_usage_payload(auth_path)
                body = render_html_dashboard(
                    payload,
                    usage_payload=usage_payload,
                    warning_hours=warning_hours,
                    live=True,
                    refresh_seconds=refresh_seconds,
                )
                self.send_html(200, body)
            except Exception as error:
                self.send_html(500, render_error_html(str(error)))

    bind_port = port
    try:
        httpd = ReusableThreadingTCPServer(("127.0.0.1", bind_port), DashboardHandler)
    except OSError:
        if bind_port == 0:
            raise
        httpd = ReusableThreadingTCPServer(("127.0.0.1", 0), DashboardHandler)

    actual_port = int(httpd.server_address[1])
    url = f"http://127.0.0.1:{actual_port}/"
    print(f"Codex reset expiry dashboard is running at {url}", flush=True)
    print("Read-only mode. Redeem resets only inside the Codex app.", flush=True)
    print(
        "Leave this window open while using the live dashboard. Press Ctrl-C to stop.",
        flush=True,
    )
    if open_browser:
        webbrowser.open(url)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped Codex reset expiry dashboard.")
    finally:
        httpd.server_close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Show when your banked Codex reset credits expire."
    )
    parser.add_argument(
        "--auth",
        type=Path,
        default=default_auth_path(),
        help="Path to Codex auth.json. Defaults to $CODEX_HOME/auth.json or ~/.codex/auth.json.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the reset-credit response as JSON after fetching it.",
    )
    parser.add_argument(
        "--summary-json",
        action="store_true",
        help="Print a sanitized usage/reset summary for lightweight UI clients.",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Print a cleaner terminal dashboard.",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable ANSI colors in --pretty output.",
    )
    parser.add_argument(
        "--ics",
        type=Path,
        help="Write a calendar file with expiry reminders for available reset credits.",
    )
    parser.add_argument(
        "--html",
        type=Path,
        help="Write a local HTML dashboard snapshot.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress terminal output when writing files.",
    )
    parser.add_argument(
        "--remind-hours",
        type=parse_hours,
        default=[72, 24, 6],
        help="Comma-separated reminder offsets for --ics. Default: 72,24,6.",
    )
    parser.add_argument(
        "--warning-hours",
        type=float,
        default=48,
        help="Mark available resets expiring within this many hours. Default: 48.",
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help="Run a local read-only browser dashboard on 127.0.0.1.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="Port for --serve. Falls back to a random port if busy. Default: 8765.",
    )
    parser.add_argument(
        "--open",
        action="store_true",
        help="Open the --serve dashboard in your browser.",
    )
    parser.add_argument(
        "--refresh-seconds",
        type=int,
        default=300,
        help="Browser auto-refresh interval for --serve. Default: 300.",
    )
    args = parser.parse_args(argv)

    if args.serve:
        return serve_dashboard(
            args.auth,
            args.port,
            args.warning_hours,
            args.refresh_seconds,
            args.open,
        )

    try:
        payload = fetch_reset_payload(args.auth)
    except FileNotFoundError:
        print(f"Could not find {args.auth}. Sign in to Codex first.", file=sys.stderr)
        return 2
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")
        print(f"Request failed with HTTP {error.code}: {detail[:300]}", file=sys.stderr)
        return 2
    except Exception as error:
        print(f"Could not check reset expiry: {error}", file=sys.stderr)
        return 2

    usage_payload = try_fetch_usage_payload(args.auth)

    should_print = not args.quiet or (not args.ics and not args.html)

    if args.summary_json:
        print(
            json.dumps(
                build_summary_payload(payload, usage_payload=usage_payload),
                indent=2,
                sort_keys=True,
            )
        )
    elif args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    elif args.pretty and should_print:
        print(
            render_pretty_report(
                payload,
                usage_payload=usage_payload,
                warning_hours=args.warning_hours,
                color=not args.no_color,
            )
        )
    elif should_print:
        print(
            render_report(
                payload,
                usage_payload=usage_payload,
                warning_hours=args.warning_hours,
            )
        )

    if args.ics:
        args.ics.expanduser().write_text(build_ics(payload, args.remind_hours))
        if should_print:
            print(f"\nWrote calendar reminders: {args.ics}")

    if args.html:
        args.html.expanduser().write_text(
            render_html_dashboard(
                payload,
                usage_payload=usage_payload,
                warning_hours=args.warning_hours,
            )
        )
        if should_print:
            print(f"\nWrote HTML dashboard: {args.html}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
