#!/usr/bin/env python3
"""
Read-only Codex reset expiry checker.

This uses your existing local Codex login to show when banked reset credits
expire. It does not redeem anything.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import html
import http.server
import json
import os
import shutil
import socketserver
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


RESET_CREDITS_URL = "https://chatgpt.com/backend-api/wham/rate-limit-reset-credits"
USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
RESET_CREDITS_HOST = "chatgpt.com"
HISTORY_CACHE_VERSION = 1
DEFAULT_HISTORY_DAYS = 30
HISTORY_CACHE_NAME = "codex-usage-reset-history-v1.json.gz"
LEGACY_HISTORY_CACHE_NAME = "codex-usage-reset-history-v1.json"
TOKEN_FIELDS = (
    ("input_tokens", "input"),
    ("cached_input_tokens", "cachedInput"),
    ("output_tokens", "output"),
    ("reasoning_output_tokens", "reasoningOutput"),
    ("total_tokens", "total"),
)


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


def default_codex_home() -> Path:
    codex_home = os.environ.get("CODEX_HOME")
    if codex_home:
        return Path(codex_home).expanduser()
    return Path("~/.codex").expanduser()


def default_history_cache_path(codex_home: Path | None = None) -> Path:
    return (codex_home or default_codex_home()) / HISTORY_CACHE_NAME


def latest_local_model(codex_home: Path | None = None) -> dict[str, Any] | None:
    codex_home = (codex_home or default_codex_home()).expanduser()
    database_paths = [
        codex_home / "state_5.sqlite",
        codex_home / "sqlite" / "state_5.sqlite",
    ]
    for database_path in database_paths:
        if not database_path.is_file():
            continue
        connection = None
        try:
            connection = sqlite3.connect(
                f"file:{database_path}?mode=ro",
                uri=True,
                timeout=1,
            )
            row = connection.execute(
                """
                SELECT model, reasoning_effort
                FROM threads
                WHERE model IS NOT NULL AND model <> ''
                ORDER BY recency_at_ms DESC, updated_at_ms DESC
                LIMIT 1
                """
            ).fetchone()
        except (OSError, sqlite3.Error):
            continue
        finally:
            if connection is not None:
                connection.close()
        if row and row[0]:
            return {
                "name": str(row[0]),
                "effort": str(row[1]) if row[1] else None,
            }
    return None


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


def safe_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def discover_rollout_files(codex_home: Path) -> dict[str, Path]:
    candidates: list[Path] = []
    sessions = codex_home / "sessions"
    archived = codex_home / "archived_sessions"
    if sessions.is_dir():
        candidates.extend(sessions.rglob("rollout-*.jsonl"))
    if archived.is_dir():
        candidates.extend(archived.glob("rollout-*.jsonl"))

    selected: dict[str, Path] = {}
    for path in candidates:
        try:
            stat = path.stat()
        except OSError:
            continue
        key = path.stem
        existing = selected.get(key)
        if existing is None:
            selected[key] = path
            continue
        try:
            existing_stat = existing.stat()
        except OSError:
            selected[key] = path
            continue
        if (stat.st_size, stat.st_mtime_ns) > (
            existing_stat.st_size,
            existing_stat.st_mtime_ns,
        ):
            selected[key] = path
    return selected


def rollout_prefix_hash(path: Path) -> str:
    with path.open("rb") as handle:
        prefix = handle.read(4096)
    newline = prefix.find(b"\n")
    if newline >= 0:
        prefix = prefix[: newline + 1]
    return hashlib.sha256(prefix).hexdigest()


def compact_rate_window(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    used_percent = safe_float(raw.get("used_percent"))
    if used_percent is None:
        return None
    window_minutes = raw.get("window_minutes")
    resets_at = raw.get("resets_at")
    return {
        "usedPercent": used_percent,
        "windowMinutes": safe_int(window_minutes) if window_minutes is not None else None,
        "resetsAt": safe_int(resets_at) if resets_at is not None else None,
    }


def scan_rollout_file(
    path: Path,
    session_key: str,
    start_offset: int = 0,
    initial_model: str | None = None,
    initial_effort: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    events: list[dict[str, Any]] = []
    model = initial_model
    effort = initial_effort
    final_offset = max(0, start_offset)

    with path.open("rb") as handle:
        handle.seek(final_offset)
        while True:
            position = handle.tell()
            line = handle.readline()
            if not line:
                final_offset = handle.tell()
                break
            if not line.endswith(b"\n"):
                final_offset = position
                break
            final_offset = handle.tell()
            if b"turn_context" not in line and b"token_count" not in line:
                continue
            try:
                record = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue

            payload = record.get("payload")
            if not isinstance(payload, dict):
                continue
            if record.get("type") == "turn_context":
                raw_model = payload.get("model")
                raw_effort = payload.get("effort")
                if isinstance(raw_model, str) and raw_model:
                    model = raw_model
                effort = raw_effort if isinstance(raw_effort, str) and raw_effort else None
                continue
            if record.get("type") != "event_msg" or payload.get("type") != "token_count":
                continue

            info = payload.get("info")
            info = info if isinstance(info, dict) else {}
            raw_tokens = info.get("last_token_usage")
            raw_tokens = raw_tokens if isinstance(raw_tokens, dict) else {}
            tokens = {
                compact_name: safe_int(raw_tokens.get(source_name))
                for source_name, compact_name in TOKEN_FIELDS
            }
            raw_limits = payload.get("rate_limits")
            raw_limits = raw_limits if isinstance(raw_limits, dict) else {}
            primary = compact_rate_window(raw_limits.get("primary"))
            secondary = compact_rate_window(raw_limits.get("secondary"))
            if not any(tokens.values()) and primary is None and secondary is None:
                continue

            timestamp = record.get("timestamp")
            if not isinstance(timestamp, str) or not timestamp:
                continue
            events.append(
                {
                    "timestamp": timestamp,
                    "session": session_key,
                    "position": position,
                    "model": model or "Unknown",
                    "effort": effort,
                    "tokens": tokens,
                    "limitId": raw_limits.get("limit_id"),
                    "primary": primary,
                    "secondary": secondary,
                }
            )

    return events, {"offset": final_offset, "model": model, "effort": effort}


def empty_history_cache() -> dict[str, Any]:
    return {"version": HISTORY_CACHE_VERSION, "files": {}, "events": []}


def load_history_cache(path: Path) -> dict[str, Any]:
    try:
        if path.suffix == ".gz":
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                cache = json.load(handle)
        else:
            cache = json.loads(path.read_text())
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return empty_history_cache()
    if not isinstance(cache, dict) or cache.get("version") != HISTORY_CACHE_VERSION:
        return empty_history_cache()
    if not isinstance(cache.get("files"), dict) or not isinstance(cache.get("events"), list):
        return empty_history_cache()
    return cache


def save_history_cache(path: Path, cache: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        if path.suffix == ".gz":
            with gzip.open(
                temporary,
                "wt",
                encoding="utf-8",
                compresslevel=5,
            ) as handle:
                json.dump(cache, handle, separators=(",", ":"))
        else:
            temporary.write_text(json.dumps(cache, separators=(",", ":")))
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def collect_model_history(
    codex_home: Path | None = None,
    cache_path: Path | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    codex_home = (codex_home or default_codex_home()).expanduser()
    cache_path = (cache_path or default_history_cache_path(codex_home)).expanduser()
    default_cache = default_history_cache_path(codex_home)
    legacy_cache = codex_home / LEGACY_HISTORY_CACHE_NAME
    if cache_path == default_cache and not cache_path.exists() and legacy_cache.exists():
        cache = load_history_cache(legacy_cache)
        save_history_cache(cache_path, cache)
        try:
            legacy_cache.unlink()
        except OSError:
            pass
    else:
        cache = load_history_cache(cache_path)
    cached_files = cache["files"]
    cached_events = [event for event in cache["events"] if isinstance(event, dict)]
    rollout_files = discover_rollout_files(codex_home)
    valid_sessions = set(rollout_files)
    cached_events = [
        event for event in cached_events if event.get("session") in valid_sessions
    ]
    next_files: dict[str, Any] = {}
    new_event_count = 0
    files_read = 0

    for session_key, path in sorted(rollout_files.items()):
        try:
            size = path.stat().st_size
            prefix_hash = rollout_prefix_hash(path)
        except OSError:
            continue
        previous = cached_files.get(session_key)
        previous = previous if isinstance(previous, dict) else {}
        offset = safe_int(previous.get("offset"))
        reset_scan = size < offset or previous.get("prefixHash") != prefix_hash
        if reset_scan:
            offset = 0
            cached_events = [
                event for event in cached_events if event.get("session") != session_key
            ]
        initial_model = previous.get("model") if not reset_scan else None
        initial_effort = previous.get("effort") if not reset_scan else None
        if not isinstance(initial_model, str):
            initial_model = None
        if not isinstance(initial_effort, str):
            initial_effort = None

        if size > offset:
            files_read += 1
            new_events, state = scan_rollout_file(
                path,
                session_key,
                start_offset=offset,
                initial_model=initial_model,
                initial_effort=initial_effort,
            )
            cached_events.extend(new_events)
            new_event_count += len(new_events)
        else:
            state = {
                "offset": offset,
                "model": initial_model,
                "effort": initial_effort,
            }
        next_files[session_key] = {
            "offset": state["offset"],
            "model": state.get("model"),
            "effort": state.get("effort"),
            "prefixHash": prefix_hash,
        }

    deduplicated = {
        (event.get("session"), safe_int(event.get("position"))): event
        for event in cached_events
        if isinstance(event.get("timestamp"), str)
    }
    events = sorted(
        deduplicated.values(),
        key=lambda event: (
            event.get("timestamp", ""),
            event.get("session", ""),
            safe_int(event.get("position")),
        ),
    )
    cache_changed = (
        new_event_count > 0
        or next_files != cached_files
        or len(events) != len(cache["events"])
    )
    if cache_changed:
        save_history_cache(
            cache_path,
            {"version": HISTORY_CACHE_VERSION, "files": next_files, "events": events},
        )
    return events, {
        "filesSeen": len(rollout_files),
        "filesRead": files_read,
        "newEvents": new_event_count,
        "cachedEvents": len(events),
    }


def parse_history_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return parse_datetime(value)
    except (TypeError, ValueError):
        return None


def model_stats_template(model: str) -> dict[str, Any]:
    return {
        "model": model,
        "responses": 0,
        "inputTokens": 0,
        "cachedInputTokens": 0,
        "uncachedInputTokens": 0,
        "outputTokens": 0,
        "reasoningOutputTokens": 0,
        "totalTokens": 0,
        "primaryDelta": 0.0,
        "secondaryDelta": 0.0,
        "primaryDeltaEvents": 0,
        "secondaryDeltaEvents": 0,
        "efforts": {},
        "firstAt": None,
        "lastAt": None,
    }


def history_window_identity(event: dict[str, Any], name: str) -> tuple[Any, Any, Any] | None:
    window = event.get(name)
    if not isinstance(window, dict) or safe_float(window.get("usedPercent")) is None:
        return None
    return (
        event.get("limitId"),
        window.get("windowMinutes"),
        window.get("resetsAt"),
    )


def sample_timeline(points: list[dict[str, Any]], limit: int = 160) -> list[dict[str, Any]]:
    if len(points) <= limit:
        return points
    indexes = {round(index * (len(points) - 1) / (limit - 1)) for index in range(limit)}
    return [point for index, point in enumerate(points) if index in indexes]


def build_model_usage_report(
    events: list[dict[str, Any]],
    now: datetime | None = None,
    history_days: int = DEFAULT_HISTORY_DAYS,
    collection_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    cutoff = now - timedelta(days=history_days) if history_days > 0 else None

    selected: list[tuple[datetime, dict[str, Any]]] = []
    all_timestamps: list[datetime] = []
    for event in events:
        timestamp = parse_history_timestamp(event.get("timestamp"))
        if timestamp is None:
            continue
        all_timestamps.append(timestamp)
        if cutoff is not None and timestamp < cutoff:
            continue
        selected.append((timestamp, event))
    selected.sort(
        key=lambda item: (
            item[0],
            item[1].get("session", ""),
            safe_int(item[1].get("position")),
        )
    )

    by_model: dict[str, dict[str, Any]] = {}
    total_tokens = 0
    known_model_tokens = 0
    current_model = None
    current_effort = None
    for timestamp, event in selected:
        model = event.get("model")
        model = model if isinstance(model, str) and model else "Unknown"
        stats = by_model.setdefault(model, model_stats_template(model))
        tokens = event.get("tokens")
        tokens = tokens if isinstance(tokens, dict) else {}
        input_tokens = safe_int(tokens.get("input"))
        cached_tokens = min(input_tokens, safe_int(tokens.get("cachedInput")))
        output_tokens = safe_int(tokens.get("output"))
        reasoning_tokens = min(output_tokens, safe_int(tokens.get("reasoningOutput")))
        event_total = safe_int(tokens.get("total"))
        if event_total == 0:
            event_total = input_tokens + output_tokens

        if event_total > 0:
            stats["responses"] += 1
            stats["inputTokens"] += input_tokens
            stats["cachedInputTokens"] += cached_tokens
            stats["uncachedInputTokens"] += max(0, input_tokens - cached_tokens)
            stats["outputTokens"] += output_tokens
            stats["reasoningOutputTokens"] += reasoning_tokens
            stats["totalTokens"] += event_total
            total_tokens += event_total
            if model != "Unknown":
                known_model_tokens += event_total
        effort = event.get("effort")
        if isinstance(effort, str) and effort:
            stats["efforts"][effort] = stats["efforts"].get(effort, 0) + 1
        timestamp_text = event.get("timestamp")
        if stats["firstAt"] is None:
            stats["firstAt"] = timestamp_text
        stats["lastAt"] = timestamp_text
        if model != "Unknown":
            current_model = model
            current_effort = effort if isinstance(effort, str) else None

    previous_windows: dict[str, dict[str, Any] | None] = {
        "primary": None,
        "secondary": None,
    }
    for _timestamp, event in selected:
        model = event.get("model")
        model = model if isinstance(model, str) and model else "Unknown"
        stats = by_model.setdefault(model, model_stats_template(model))
        for name in ("primary", "secondary"):
            window = event.get(name)
            identity = history_window_identity(event, name)
            if identity is None or not isinstance(window, dict):
                continue
            used = safe_float(window.get("usedPercent"))
            if used is None:
                continue
            previous = previous_windows[name]
            if previous is None or previous["identity"] != identity:
                previous_windows[name] = {"identity": identity, "used": used}
                continue
            delta = used - previous["used"]
            if delta > 0:
                field = "primaryDelta" if name == "primary" else "secondaryDelta"
                count_field = (
                    "primaryDeltaEvents" if name == "primary" else "secondaryDeltaEvents"
                )
                stats[field] += delta
                stats[count_field] += 1
                previous["used"] = used

    models = []
    for stats in by_model.values():
        input_tokens = stats["inputTokens"]
        output_tokens = stats["outputTokens"]
        model_tokens = stats["totalTokens"]
        stats["cachedShare"] = round(
            (stats["cachedInputTokens"] / input_tokens) * 100, 1
        ) if input_tokens else 0.0
        stats["reasoningShare"] = round(
            (stats["reasoningOutputTokens"] / output_tokens) * 100, 1
        ) if output_tokens else 0.0
        stats["averageTokens"] = round(model_tokens / stats["responses"]) if stats["responses"] else 0
        stats["primaryDelta"] = round(stats["primaryDelta"], 4)
        stats["secondaryDelta"] = round(stats["secondaryDelta"], 4)
        stats["primaryPerMillion"] = round(
            (stats["primaryDelta"] / model_tokens) * 1_000_000, 4
        ) if model_tokens else None
        stats["secondaryPerMillion"] = round(
            (stats["secondaryDelta"] / model_tokens) * 1_000_000, 4
        ) if model_tokens else None
        stats["quotaCost"] = {
            "fiveHour": {
                "quotaPointsObserved": stats["primaryDelta"],
                "positiveIncreaseEvents": stats["primaryDeltaEvents"],
                "quotaPointsPerMillionRecordedTokens": (
                    stats["primaryPerMillion"]
                    if stats["primaryDeltaEvents"] > 0
                    else None
                ),
                "recordedTokensPerQuotaPoint": (
                    round(model_tokens / stats["primaryDelta"])
                    if stats["primaryDeltaEvents"] > 0 and stats["primaryDelta"] > 0
                    else None
                ),
            },
            "weekly": {
                "quotaPointsObserved": stats["secondaryDelta"],
                "positiveIncreaseEvents": stats["secondaryDeltaEvents"],
                "quotaPointsPerMillionRecordedTokens": (
                    stats["secondaryPerMillion"]
                    if stats["secondaryDeltaEvents"] > 0
                    else None
                ),
                "recordedTokensPerQuotaPoint": (
                    round(model_tokens / stats["secondaryDelta"])
                    if stats["secondaryDeltaEvents"] > 0 and stats["secondaryDelta"] > 0
                    else None
                ),
            },
        }
        stats["efforts"] = [
            {"name": name, "responses": count}
            for name, count in sorted(
                stats["efforts"].items(), key=lambda item: (-item[1], item[0])
            )
        ]
        models.append(stats)
    models.sort(key=lambda item: (-item["totalTokens"], item["model"].lower()))
    color_by_model = {
        name: MODEL_COLORS[index % len(MODEL_COLORS)]
        for index, name in enumerate(sorted(item["model"] for item in models))
    }
    for stats in models:
        stats["color"] = color_by_model[stats["model"]]

    local_history_start = min(all_timestamps) if all_timestamps else None
    local_history_end = max(all_timestamps) if all_timestamps else None
    local_history_seconds = (
        max(0.0, (now - local_history_start).total_seconds())
        if local_history_start
        else 0.0
    )
    local_history_days = (
        max(1, int((local_history_seconds + 86_399) // 86_400))
        if local_history_start
        else 0
    )

    timeline: list[dict[str, Any]] = []
    latest_primary_identity = None
    timeline_days = history_days if history_days > 0 else local_history_days
    raw_points = []
    for timestamp, event in selected:
        identity = history_window_identity(event, "primary")
        window = event.get("primary")
        if identity is None or not isinstance(window, dict):
            continue
        latest_primary_identity = identity
        raw_points.append(
            {
                "timestamp": timestamp.isoformat(),
                "model": event.get("model") or "Unknown",
                "effort": event.get("effort"),
                "usedPercent": safe_float(window.get("usedPercent")) or 0.0,
                "windowId": "|".join("" if value is None else str(value) for value in identity),
                "windowResetAt": identity[2],
            }
        )
    compressed = []
    for point in raw_points:
        previous = compressed[-1] if compressed else None
        same_state = previous is not None and all(
            point[key] == previous[key]
            for key in ("usedPercent", "model", "windowId")
        )
        if not same_state:
            compressed.append(point)
            continue
        if len(compressed) >= 2 and all(
            compressed[-2][key] == previous[key]
            for key in ("usedPercent", "model", "windowId")
        ):
            compressed[-1] = point
        else:
            compressed.append(point)
    timeline = sample_timeline(compressed, limit=240)

    return {
        "schemaVersion": 1,
        "generatedAt": now.isoformat(),
        "historyDays": history_days,
        "eventCount": len(selected),
        "responseCount": sum(model["responses"] for model in models),
        "totalTokens": total_tokens,
        "knownModelTokenPercent": round(
            (known_model_tokens / total_tokens) * 100, 1
        ) if total_tokens else 0.0,
        "currentModel": current_model,
        "currentEffort": current_effort,
        "models": models,
        "timeline": timeline,
        "timelineDays": timeline_days,
        "timelineResetAt": latest_primary_identity[2] if latest_primary_identity else None,
        "rangeStart": selected[0][0].isoformat() if selected else None,
        "rangeEnd": selected[-1][0].isoformat() if selected else None,
        "localHistoryStart": local_history_start.isoformat() if local_history_start else None,
        "localHistoryEnd": local_history_end.isoformat() if local_history_end else None,
        "localHistoryDays": local_history_days,
        "collection": collection_meta or {},
    }


def collect_model_usage_report(
    codex_home: Path | None = None,
    cache_path: Path | None = None,
    history_days: int = DEFAULT_HISTORY_DAYS,
    now: datetime | None = None,
) -> dict[str, Any]:
    events, collection_meta = collect_model_history(codex_home, cache_path)
    return build_model_usage_report(
        events,
        now=now,
        history_days=history_days,
        collection_meta=collection_meta,
    )


def collect_model_usage_reports(
    history_ranges: list[int],
    codex_home: Path | None = None,
    cache_path: Path | None = None,
    now: datetime | None = None,
) -> dict[int, dict[str, Any]]:
    events, collection_meta = collect_model_history(codex_home, cache_path)
    now = now or datetime.now(timezone.utc)
    return {
        days: build_model_usage_report(
            events,
            now=now,
            history_days=days,
            collection_meta=collection_meta,
        )
        for days in dict.fromkeys(history_ranges)
    }


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


def format_date_only(value: datetime | None, local_tz: timezone | None = None) -> str:
    if value is None:
        return "unknown"
    return value.astimezone(local_tz).strftime("%b %-d, %Y")


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


MODEL_COLORS = (
    "#256c86",
    "#217a4b",
    "#b34a3c",
    "#9b6a00",
    "#14857b",
    "#b54f78",
    "#4d6fb3",
)


def model_color(model: str) -> str:
    index = sum((position + 1) * ord(character) for position, character in enumerate(model))
    return MODEL_COLORS[index % len(MODEL_COLORS)]


def format_token_count(value: int) -> str:
    number = max(0, int(value))
    if number >= 1_000_000_000:
        return f"{number / 1_000_000_000:.2f}B"
    if number >= 1_000_000:
        return f"{number / 1_000_000:.2f}M"
    if number >= 1_000:
        return f"{number / 1_000:.1f}K"
    return f"{number:,}"


def format_quota_rate(value: Any, delta_events: int) -> str:
    numeric = safe_float(value)
    if numeric is None or delta_events <= 0:
        return "Collecting data"
    return f"{numeric:.2f} pts / 1M tokens"


def model_quota_cost(model: dict[str, Any], period: str) -> dict[str, Any]:
    quota_cost = model.get("quotaCost")
    quota_cost = quota_cost if isinstance(quota_cost, dict) else {}
    cost = quota_cost.get(period)
    cost = cost if isinstance(cost, dict) else {}
    if period == "fiveHour":
        fallback_rate = model.get("primaryPerMillion")
        fallback_points = model.get("primaryDelta")
        fallback_events = model.get("primaryDeltaEvents")
    else:
        fallback_rate = model.get("secondaryPerMillion")
        fallback_points = model.get("secondaryDelta")
        fallback_events = model.get("secondaryDeltaEvents")
    rate = safe_float(cost.get("quotaPointsPerMillionRecordedTokens"))
    if rate is None:
        rate = safe_float(fallback_rate)
    points = safe_float(cost.get("quotaPointsObserved"))
    if points is None:
        points = safe_float(fallback_points) or 0.0
    events = safe_int(cost.get("positiveIncreaseEvents"))
    if events == 0:
        events = safe_int(fallback_events)
    tokens_per_point = safe_int(cost.get("recordedTokensPerQuotaPoint"))
    if tokens_per_point == 0 and rate is not None and rate > 0 and events > 0:
        tokens_per_point = round(1_000_000 / rate)
    available = rate is not None and rate > 0 and events > 0 and tokens_per_point > 0
    return {
        "available": available,
        "rate": rate,
        "points": points,
        "events": events,
        "tokensPerPoint": tokens_per_point,
    }


def quota_sample_copy(cost: dict[str, Any]) -> str:
    if not cost.get("available"):
        return "No observed quota increases in this range"
    points = safe_float(cost.get("points")) or 0.0
    events = safe_int(cost.get("events"))
    point_word = "point" if points == 1 else "points"
    copy = f"{points:g} {point_word} observed across {events:,} increases"
    if points < 25:
        copy += " · early sample"
    return copy


def render_quota_cost_comparison(
    models: list[dict[str, Any]],
    history_days: int,
) -> str:
    costs_by_period = {
        period: {
            str(model.get("model") or "Unknown"): model_quota_cost(model, period)
            for model in models
        }
        for period in ("fiveHour", "weekly")
    }
    ranks: dict[str, dict[str, int]] = {}
    for period, costs in costs_by_period.items():
        ranked = sorted(
            (
                (name, cost)
                for name, cost in costs.items()
                if cost.get("available")
            ),
            key=lambda item: (-(safe_float(item[1].get("rate")) or 0.0), item[0].lower()),
        )
        ranks[period] = {name: index for index, (name, _cost) in enumerate(ranked, start=1)}

    def ordering(model: dict[str, Any]) -> tuple[Any, ...]:
        name = str(model.get("model") or "Unknown")
        return (
            ranks["fiveHour"].get(name, 10_000),
            ranks["weekly"].get(name, 10_000),
            -safe_int(model.get("totalTokens")),
            name.lower(),
        )

    def cost_cell(name: str, period: str, label: str) -> str:
        cost = costs_by_period[period][name]
        rank = ranks[period].get(name)
        if not cost.get("available"):
            return f"""
              <div class="quota-cost-cell no-sample" data-label="{html_escape(label)}">
                <strong>Collecting data</strong>
                <span>{html_escape(quota_sample_copy(cost))}</span>
              </div>
            """
        rank_copy = f"#{rank} fastest" if rank == 1 else f"#{rank}"
        return f"""
          <div class="quota-cost-cell" data-label="{html_escape(label)}">
            <span class="quota-rank{' fastest' if rank == 1 else ''}">{rank_copy}</span>
            <strong>1 quota point every {format_token_count(safe_int(cost.get('tokensPerPoint')))} tokens</strong>
            <span>{html_escape(format_quota_rate(cost.get('rate'), safe_int(cost.get('events'))))}</span>
            <span>{html_escape(quota_sample_copy(cost))}</span>
          </div>
        """

    rows = []
    for model in sorted(models, key=ordering):
        name = str(model.get("model") or "Unknown")
        color = str(model.get("color") or model_color(name))
        rows.append(
            f"""
            <div class="quota-cost-row">
              <div class="quota-cost-model">
                <i style="background:{html_escape(color)}"></i>
                <strong>{html_escape(name)}</strong>
              </div>
              {cost_cell(name, "fiveHour", "5-hour quota")}
              {cost_cell(name, "weekly", "Weekly quota")}
            </div>
            """
        )

    return f"""
      <section class="quota-comparison" aria-labelledby="quota-cost-heading-{history_days}">
        <div class="quota-comparison-heading">
          <div>
            <h3 id="quota-cost-heading-{history_days}">Quota cost by model</h3>
            <p>Lower tokens per quota point means faster drain. One point equals 1% on that usage gauge.</p>
          </div>
          <span>Observed estimate</span>
        </div>
        <div class="quota-cost-header" aria-hidden="true">
          <span>Model</span><span>5-hour quota</span><span>Weekly quota</span>
        </div>
        {"".join(rows)}
      </section>
    """


def render_quota_timeline_svg(
    model_report: dict[str, Any],
    local_tz: timezone | None = None,
) -> str:
    model_colors = {
        str(item.get("model")): str(item.get("color"))
        for item in model_report.get("models", [])
        if isinstance(item, dict) and item.get("model") and item.get("color")
    }

    def color_for(model: str) -> str:
        return model_colors.get(model, model_color(model))

    raw_points = model_report.get("timeline")
    raw_points = raw_points if isinstance(raw_points, list) else []
    points = []
    for raw in raw_points:
        if not isinstance(raw, dict):
            continue
        timestamp = parse_history_timestamp(raw.get("timestamp"))
        used = safe_float(raw.get("usedPercent"))
        if timestamp is None or used is None:
            continue
        model = raw.get("model")
        model = model if isinstance(model, str) and model else "Unknown"
        window_id = raw.get("windowId")
        window_id = window_id if isinstance(window_id, str) else ""
        points.append((timestamp, max(0.0, min(100.0, used)), model, window_id))
    if not points:
        return '<div class="chart-empty">No 5-hour quota trajectory is recorded in this range yet.</div>'

    width = 820
    height = 250
    left = 52
    right = 18
    top = 18
    bottom = 38
    plot_width = width - left - right
    plot_height = height - top - bottom
    first_time = points[0][0].timestamp()
    last_time = points[-1][0].timestamp()
    span = max(1.0, last_time - first_time)

    coordinates = []
    for index, (timestamp, used, model, window_id) in enumerate(points):
        if len(points) == 1:
            x = left + plot_width / 2
        else:
            x = left + ((timestamp.timestamp() - first_time) / span) * plot_width
        y = top + ((100.0 - used) / 100.0) * plot_height
        coordinates.append((x, y, timestamp, used, model, window_id, index))

    grid = []
    for used in (100, 75, 50, 25, 0):
        y = top + ((100 - used) / 100) * plot_height
        grid.append(
            f'<line class="chart-grid" x1="{left}" y1="{y:.2f}" x2="{width - right}" y2="{y:.2f}" />'
            f'<text class="chart-axis" x="{left - 9}" y="{y + 4:.2f}" text-anchor="end">{used}%</text>'
        )

    segments = []
    for previous, current in zip(coordinates, coordinates[1:]):
        if previous[5] != current[5]:
            continue
        color = color_for(current[4])
        segments.append(
            f'<line class="chart-segment" x1="{previous[0]:.2f}" y1="{previous[1]:.2f}" '
            f'x2="{current[0]:.2f}" y2="{current[1]:.2f}" stroke="{color}" />'
        )

    dots = []
    for x, y, timestamp, used, model, _window_id, _index in coordinates:
        label = (
            f"{model}: {used:g}% used at "
            f"{format_dt_compact(timestamp, local_tz)}"
        )
        dots.append(
            f'<circle class="chart-dot" cx="{x:.2f}" cy="{y:.2f}" r="4.5" '
            f'fill="{color_for(model)}"><title>{html_escape(label)}</title></circle>'
        )

    start_label = format_dt_compact(points[0][0], local_tz)
    end_label = format_dt_compact(points[-1][0], local_tz)
    legend_models = []
    seen_models = set()
    for _timestamp, _used, model, _window_id in points:
        if model not in seen_models:
            seen_models.add(model)
            legend_models.append(model)
    legend = "".join(
        f'<span class="legend-item"><i style="background:{color_for(model)}"></i>{html_escape(model)}</span>'
        for model in legend_models
    )

    return f"""
      <div class="timeline-chart">
        <svg viewBox="0 0 {width} {height}" role="img" aria-label="5-hour quota percentage used over time by model">
          {"".join(grid)}
          {"".join(segments)}
          {"".join(dots)}
          <text class="chart-axis chart-time" x="{left}" y="{height - 10}">{html_escape(start_label)}</text>
          <text class="chart-axis chart-time" x="{width - right}" y="{height - 10}" text-anchor="end">{html_escape(end_label)}</text>
        </svg>
        <div class="chart-legend">{legend}</div>
      </div>
    """


def render_model_usage_section(
    model_report: dict[str, Any] | None,
    local_tz: timezone | None = None,
    live: bool = False,
    interactive_static: bool = False,
) -> str:
    if not model_report:
        return ""
    models = model_report.get("models")
    models = models if isinstance(models, list) else []
    history_days = safe_int(model_report.get("historyDays"))
    local_history_days = safe_int(model_report.get("localHistoryDays"))
    local_day_word = "day" if local_history_days == 1 else "days"
    if history_days == 0:
        range_label = f"All local history · {local_history_days} {local_day_word} available"
    elif local_history_days and local_history_days < history_days:
        range_label = (
            f"Last {history_days} days requested · "
            f"{local_history_days} {local_day_word} available locally"
        )
    else:
        range_label = f"Last {history_days} days"
    local_start = parse_history_timestamp(model_report.get("localHistoryStart"))
    local_end = parse_history_timestamp(model_report.get("localHistoryEnd"))
    local_span = (
        f"Local records: {format_date_only(local_start, local_tz)} through "
        f"{format_date_only(local_end, local_tz)}"
        if local_start and local_end
        else "No local rollout records found"
    )
    current_model = model_report.get("currentModel") or "Not identified yet"
    current_effort = model_report.get("currentEffort")
    current_label = str(current_model)
    if isinstance(current_effort, str) and current_effort:
        current_label += f" · {current_effort}"

    range_options = []
    for days, label in ((7, "7d"), (30, "30d"), (90, "90d"), (0, "All")):
        selected = days == history_days
        if live:
            class_name = "range-option selected" if selected else "range-option"
            range_options.append(
                f'<a class="{class_name}" href="/?days={days}">{label}</a>'
            )
        elif interactive_static:
            class_name = "range-option selected" if selected else "range-option"
            pressed = "true" if selected else "false"
            range_options.append(
                f'<button class="{class_name}" type="button" data-history-range="{days}" '
                f'aria-pressed="{pressed}">{label}</button>'
            )
        else:
            class_name = "range-option selected" if selected else "range-option disabled"
            range_options.append(f'<span class="{class_name}">{label}</span>')
    if live:
        export_href = f"/model-usage.json?days={history_days}"
    else:
        export_json = json.dumps(model_report, indent=2, sort_keys=True) + "\n"
        export_href = "data:application/json;charset=utf-8," + urllib.parse.quote(
            export_json,
            safe="",
        )
    export_link = (
        f'<a class="export-link" href="{html_escape(export_href)}" '
        'download="codex-model-usage-report.json">Export JSON</a>'
    )

    if not models:
        collection = model_report.get("collection")
        collection = collection if isinstance(collection, dict) else {}
        history_error = collection.get("error")
        empty_title = "Could not read local model history" if history_error else "No model token records in this range"
        empty_copy = (
            str(history_error)
            if history_error
            else "Use Codex normally, then refresh this report. New responses will appear automatically."
        )
        model_content = f"""
          <section class="history-empty">
            <h3>{html_escape(empty_title)}</h3>
            <p>{html_escape(empty_copy)}</p>
          </section>
        """
        quota_comparison = ""
    else:
        max_tokens = max(safe_int(model.get("totalTokens")) for model in models) or 1
        five_hour_rates = [
            safe_float(model_quota_cost(model, "fiveHour").get("rate"))
            for model in models
            if model_quota_cost(model, "fiveHour").get("available")
        ]
        weekly_rates = [
            safe_float(model_quota_cost(model, "weekly").get("rate"))
            for model in models
            if model_quota_cost(model, "weekly").get("available")
        ]
        max_five_hour_rate = max(five_hour_rates) if five_hour_rates else 1.0
        max_weekly_rate = max(weekly_rates) if weekly_rates else 1.0
        quota_comparison = render_quota_cost_comparison(models, history_days)
        model_rows = []
        for model in models:
            name = str(model.get("model") or "Unknown")
            color = str(model.get("color") or model_color(name))
            total_tokens = safe_int(model.get("totalTokens"))
            response_count = safe_int(model.get("responses"))
            response_word = "response" if response_count == 1 else "responses"
            token_width = max(1.5, (total_tokens / max_tokens) * 100) if total_tokens else 0
            five_hour_cost = model_quota_cost(model, "fiveHour")
            weekly_cost = model_quota_cost(model, "weekly")
            five_hour_rate = safe_float(five_hour_cost.get("rate"))
            weekly_rate = safe_float(weekly_cost.get("rate"))
            five_hour_width = (
                max(1.5, (five_hour_rate / max_five_hour_rate) * 100)
                if five_hour_cost.get("available") and five_hour_rate is not None and max_five_hour_rate
                else 0
            )
            weekly_width = (
                max(1.5, (weekly_rate / max_weekly_rate) * 100)
                if weekly_cost.get("available") and weekly_rate is not None and max_weekly_rate
                else 0
            )
            effort_items = model.get("efforts")
            effort_items = effort_items if isinstance(effort_items, list) else []
            effort_copy = ", ".join(
                f"{item.get('name')} {safe_int(item.get('responses')):,}"
                for item in effort_items[:3]
                if isinstance(item, dict) and item.get("name")
            ) or "not recorded"
            primary_delta = safe_float(model.get("primaryDelta")) or 0.0
            secondary_delta = safe_float(model.get("secondaryDelta")) or 0.0
            model_rows.append(
                f"""
                <section class="model-row">
                  <div class="model-row-head">
                    <div>
                      <h3><i style="background:{html_escape(color)}"></i>{html_escape(name)}</h3>
                      <p>{response_count:,} model {response_word} · effort {html_escape(effort_copy)}</p>
                    </div>
                    <strong>{format_token_count(total_tokens)} tokens</strong>
                  </div>
                  <div class="metric-bar-row">
                    <span>Recorded token volume</span>
                    <div class="metric-track"><i style="width:{token_width:.2f}%;background:{html_escape(color)}"></i></div>
                    <strong>{format_token_count(total_tokens)}</strong>
                  </div>
                  <div class="metric-bar-row">
                    <span>5-hour quota cost</span>
                    <div class="metric-track quota"><i style="width:{five_hour_width:.2f}%;background:{html_escape(color)}"></i></div>
                    <strong>{html_escape(format_quota_rate(five_hour_rate, safe_int(five_hour_cost.get('events'))))}</strong>
                  </div>
                  <div class="metric-bar-row">
                    <span>Weekly quota cost</span>
                    <div class="metric-track quota"><i style="width:{weekly_width:.2f}%;background:{html_escape(color)}"></i></div>
                    <strong>{html_escape(format_quota_rate(weekly_rate, safe_int(weekly_cost.get('events'))))}</strong>
                  </div>
                  <div class="model-facts">
                    <span>{safe_float(model.get('cachedShare')) or 0:g}% of input was cached</span>
                    <span>{format_token_count(safe_int(model.get('averageTokens')))} tokens / model response</span>
                    <span>{primary_delta:g} 5-hour quota points observed</span>
                    <span>{secondary_delta:g} weekly quota points observed</span>
                  </div>
                </section>
                """
            )
        model_content = "".join(model_rows)

    timeline = render_quota_timeline_svg(model_report, local_tz)
    timeline_days = safe_int(model_report.get("timelineDays"))
    if history_days == 0:
        timeline_range_copy = (
            f"All {timeline_days} {'day' if timeline_days == 1 else 'days'} "
            "of locally available windows"
            if timeline_days
            else "All locally available windows"
        )
    elif local_history_days and local_history_days < history_days:
        timeline_range_copy = (
            f"Last {history_days} days requested · "
            f"{local_history_days} {local_day_word} available locally"
        )
    else:
        timeline_range_copy = f"Last {history_days} days of recorded windows"
    reset_copy = f"{timeline_range_copy} · gaps mark quota resets"

    return f"""
    <section class="history-section">
      <div class="section-heading">
        <div>
          <p class="eyebrow">Local history · {html_escape(range_label)}</p>
          <h2>Model usage</h2>
          <p class="section-copy">Compare recorded token volume with observed changes in your Codex quota.</p>
          <p class="section-data-span">{html_escape(local_span)}</p>
        </div>
        <div class="history-controls">
          <nav class="range-control" aria-label="History range">{"".join(range_options)}</nav>
          {export_link}
        </div>
      </div>

      <section class="history-summary">
        <div><span>Latest model</span><strong>{html_escape(current_label)}</strong></div>
        <div><span>Recorded tokens</span><strong>{format_token_count(safe_int(model_report.get('totalTokens')))}</strong></div>
        <div><span>Model responses</span><strong>{safe_int(model_report.get('responseCount')):,}</strong></div>
        <div><span>Model coverage</span><strong>{safe_float(model_report.get('knownModelTokenPercent')) or 0:g}%</strong></div>
      </section>

      {quota_comparison}

      <section class="history-panel">
        <div class="panel-heading">
          <div>
            <h3>Quota trajectory</h3>
            <p>{html_escape(reset_copy)}</p>
          </div>
          <span>5-hour usage %</span>
        </div>
        {timeline}
      </section>

      <section class="metric-definitions" aria-labelledby="metric-definitions-{history_days}">
        <h3 id="metric-definitions-{history_days}">Metric definitions</h3>
        <dl>
          <div><dt>Recorded tokens</dt><dd>Input plus output tokens reported after each model response. Cached input is included in this total.</dd></div>
          <div><dt>Cached input</dt><dd>The share of input context Codex marked as reused from cache. A high share is normal in long, tool-using tasks.</dd></div>
          <div><dt>5-hour quota cost</dt><dd>Observed increases in the 5-hour usage gauge divided by recorded tokens. Fewer tokens per point means faster drain.</dd></div>
          <div><dt>Weekly quota cost</dt><dd>The same observed calculation using the weekly usage gauge. Its rate and sample are tracked separately.</dd></div>
          <div><dt>Quota point</dt><dd>One percentage point on a usage gauge, such as a move from 20% used to 21% used.</dd></div>
          <div><dt>Points observed</dt><dd>The sample behind a cost estimate. Small samples are marked early and should not drive a strong conclusion.</dd></div>
          <div><dt>Model responses</dt><dd>Individual model steps, including tool-call steps. This count is larger than the number of messages or tasks.</dd></div>
          <div><dt>Model coverage</dt><dd>The share of recorded tokens whose rollout entry identified a model. Unknown model entries are kept separate.</dd></div>
        </dl>
      </section>

      <div class="model-list">{model_content}</div>

      <p class="method-note"><strong>Methodology:</strong> Actual token counts come from Codex's local per-response records. The 5-hour and weekly gauges are rounded, account-wide snapshots, so model attribution is an observed estimate. Overlapping tasks and work on another device can affect the percentage between samples. In the comparison, fewer recorded tokens per quota point means faster observed drain.</p>
    </section>
    """


def render_model_usage_variants(
    model_report: dict[str, Any] | None,
    model_reports: dict[int, dict[str, Any]] | None,
    local_tz: timezone | None = None,
    live: bool = False,
) -> str:
    if live or not model_reports:
        return render_model_usage_section(model_report, local_tz, live)

    active_days = safe_int((model_report or {}).get("historyDays"))
    if active_days not in model_reports:
        active_days = 30 if 30 in model_reports else next(iter(model_reports))
    ordered_days = [days for days in (7, 30, 90, 0) if days in model_reports]
    ordered_days.extend(days for days in model_reports if days not in ordered_days)
    variants = []
    for days in ordered_days:
        hidden = "" if days == active_days else " hidden"
        variants.append(
            f'<div class="history-variant" data-history-panel="{days}"{hidden}>'
            + render_model_usage_section(
                model_reports[days],
                local_tz,
                live=False,
                interactive_static=True,
            )
            + "</div>"
        )

    script = """
    <script>
      (() => {
        for (const root of document.querySelectorAll("[data-history-variants]")) {
          const activate = (days, updateHash) => {
            const value = String(days);
            for (const panel of root.querySelectorAll("[data-history-panel]")) {
              panel.hidden = panel.dataset.historyPanel !== value;
            }
            for (const button of root.querySelectorAll("[data-history-range]")) {
              const selected = button.dataset.historyRange === value;
              button.classList.toggle("selected", selected);
              button.setAttribute("aria-pressed", selected ? "true" : "false");
            }
            if (updateHash) history.replaceState(null, "", "#history-" + value);
          };
          root.addEventListener("click", (event) => {
            const button = event.target.closest("[data-history-range]");
            if (button && root.contains(button)) activate(button.dataset.historyRange, true);
          });
          const match = location.hash.match(/^#history-(7|30|90|0)$/);
          activate(match ? match[1] : root.dataset.defaultHistory, false);
        }
      })();
    </script>
    """
    return (
        f'<div class="history-variants" data-history-variants '
        f'data-default-history="{active_days}">{"".join(variants)}</div>'
        + script
    )


def render_html_dashboard(
    payload: dict[str, Any],
    usage_payload: dict[str, Any] | None = None,
    model_report: dict[str, Any] | None = None,
    model_reports: dict[int, dict[str, Any]] | None = None,
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
        format_dt_compact(next_credit.expires_at, local_tz)
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
              <p class="field-label">Expires</p>
              <p class="big-date">{html_escape(format_dt_compact(credit.expires_at, local_tz))}</p>
              <div class="bar" aria-label="Reset lifetime remaining">
                <span style="width: {0 if percent_left is None else percent_left}%"></span>
              </div>
              <dl>
                <div><dt>Countdown from snapshot</dt><dd>{html_escape(format_duration_words(seconds_left or 0) if seconds_left is not None else "unknown")}</dd></div>
                <div><dt>Lifetime left</dt><dd>{html_escape(str(percent_left) + "%" if percent_left is not None else "unknown")}</dd></div>
                <div><dt>Granted</dt><dd>{html_escape(format_dt_compact(credit.granted_at, local_tz))}</dd></div>
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
              </div>
              <div class="bar" aria-label="{html_escape(window.label)} usage remaining">
                <span style="width: {window.remaining_percent}%"></span>
              </div>
              <p class="subtle">Resets {html_escape(format_reset_at_compact(window.reset_at, now, local_tz))}</p>
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
        else "Snapshot values stay fixed until a new report is generated."
    )
    mode_label = "Live local dashboard" if live else "Local snapshot"
    model_section = render_model_usage_variants(
        model_report,
        model_reports,
        local_tz,
        live,
    )
    refresh_action = (
        f'<a class="button" href="/?days={safe_int(model_report.get("historyDays")) if model_report else DEFAULT_HISTORY_DAYS}">Refresh</a>'
        if live
        else ""
    )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  {refresh_meta}
  <title>Codex Usage Report</title>
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
    .summary-card, .reset-card, .usage-card, .note, .history-panel, .model-row, .history-empty {{
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
      letter-spacing: 0;
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
    .field-label {{
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
      margin: 0 0 4px;
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
    .history-section {{
      margin-top: 38px;
      padding-top: 28px;
      border-top: 1px solid var(--line);
    }}
    .section-heading {{
      display: flex;
      align-items: end;
      justify-content: space-between;
      gap: 18px;
      margin-bottom: 16px;
    }}
    .section-heading h2 {{
      font-size: 24px;
      line-height: 1.15;
      letter-spacing: 0;
      margin: 5px 0 6px;
    }}
    .section-copy {{
      color: var(--muted);
      font-size: 14px;
    }}
    .section-data-span {{
      color: var(--muted);
      font-size: 12px;
      margin-top: 5px;
    }}
    .range-control {{
      display: inline-grid;
      grid-template-columns: repeat(4, minmax(42px, 1fr));
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
      flex: 0 0 auto;
    }}
    .history-controls {{
      display: flex;
      align-items: center;
      justify-content: flex-end;
      gap: 10px;
      flex-wrap: wrap;
    }}
    .export-link {{
      color: var(--accent);
      font-size: 13px;
      font-weight: 700;
      text-decoration: none;
      white-space: nowrap;
    }}
    .export-link:hover {{ text-decoration: underline; }}
    .range-option {{
      color: var(--muted);
      background: var(--panel);
      border: 0;
      border-radius: 0;
      text-align: center;
      text-decoration: none;
      font: inherit;
      font-size: 13px;
      font-weight: 700;
      padding: 7px 10px;
      border-right: 1px solid var(--line);
      cursor: pointer;
    }}
    .range-option:last-child {{ border-right: 0; }}
    .range-option.selected {{
      color: var(--panel);
      background: var(--ink);
    }}
    .range-option.disabled:not(.selected) {{ opacity: .55; cursor: default; }}
    .history-variant[hidden] {{ display: none; }}
    .history-summary {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      border-top: 1px solid var(--line);
      border-bottom: 1px solid var(--line);
      margin-bottom: 14px;
    }}
    .history-summary div {{
      min-width: 0;
      padding: 14px 16px;
      border-right: 1px solid var(--line);
    }}
    .history-summary div:first-child {{ padding-left: 0; }}
    .history-summary div:last-child {{ border-right: 0; }}
    .history-summary span {{
      display: block;
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 4px;
    }}
    .history-summary strong {{
      display: block;
      font-size: 17px;
      overflow-wrap: anywhere;
    }}
    .quota-comparison {{
      border-top: 1px solid var(--line);
      border-bottom: 1px solid var(--line);
      margin: 0 0 14px;
      padding: 16px 0 4px;
    }}
    .quota-comparison-heading {{
      display: flex;
      align-items: start;
      justify-content: space-between;
      gap: 18px;
      margin-bottom: 14px;
    }}
    .quota-comparison-heading h3 {{
      font-size: 17px;
      letter-spacing: 0;
      margin: 0 0 4px;
    }}
    .quota-comparison-heading p,
    .quota-comparison-heading > span {{
      color: var(--muted);
      font-size: 12px;
    }}
    .quota-comparison-heading > span {{ white-space: nowrap; }}
    .quota-cost-header,
    .quota-cost-row {{
      display: grid;
      grid-template-columns: minmax(140px, .65fr) repeat(2, minmax(250px, 1fr));
      column-gap: 20px;
    }}
    .quota-cost-header {{
      color: var(--muted);
      font-size: 11px;
      font-weight: 750;
      text-transform: uppercase;
      padding: 0 0 8px;
    }}
    .quota-cost-row {{
      align-items: start;
      border-top: 1px solid var(--line);
      padding: 12px 0;
    }}
    .quota-cost-model {{
      display: flex;
      align-items: center;
      gap: 8px;
      min-width: 0;
      padding-top: 3px;
    }}
    .quota-cost-model i {{
      display: inline-block;
      flex: 0 0 auto;
      width: 10px;
      height: 10px;
      border-radius: 50%;
    }}
    .quota-cost-model strong {{ overflow-wrap: anywhere; }}
    .quota-cost-cell {{
      display: grid;
      gap: 3px;
      min-width: 0;
    }}
    .quota-cost-cell strong {{
      font-size: 14px;
      overflow-wrap: anywhere;
    }}
    .quota-cost-cell > span:not(.quota-rank) {{
      color: var(--muted);
      font-size: 11px;
      line-height: 1.35;
    }}
    .quota-rank {{
      color: var(--accent);
      font-size: 11px;
      font-weight: 800;
      text-transform: uppercase;
    }}
    .quota-rank.fastest {{ color: var(--red); }}
    .quota-cost-cell.no-sample strong {{ color: var(--muted); }}
    .history-panel {{
      margin-bottom: 14px;
      padding: 18px;
    }}
    .metric-definitions {{
      border-top: 1px solid var(--line);
      border-bottom: 1px solid var(--line);
      padding: 16px 0;
      margin: 0 0 14px;
    }}
    .metric-definitions h3 {{
      font-size: 15px;
      letter-spacing: 0;
      margin: 0 0 12px;
    }}
    .metric-definitions dl {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 14px 22px;
      margin: 0;
    }}
    .metric-definitions dl div {{
      display: block;
      border: 0;
      padding: 0;
    }}
    .metric-definitions dt {{
      color: var(--ink);
      font-size: 12px;
      font-weight: 750;
      margin-bottom: 4px;
    }}
    .metric-definitions dd {{
      color: var(--muted);
      font-size: 12px;
      font-weight: 400;
      line-height: 1.45;
      text-align: left;
    }}
    .panel-heading {{
      display: flex;
      justify-content: space-between;
      align-items: start;
      gap: 16px;
      margin-bottom: 10px;
    }}
    .panel-heading h3 {{
      font-size: 17px;
      letter-spacing: 0;
      margin: 0 0 4px;
    }}
    .panel-heading p, .panel-heading > span {{
      color: var(--muted);
      font-size: 12px;
    }}
    .timeline-chart svg {{
      display: block;
      width: 100%;
      aspect-ratio: 820 / 250;
      min-height: 210px;
      overflow: visible;
    }}
    .chart-grid {{ stroke: var(--line); stroke-width: 1; }}
    .chart-axis {{ fill: var(--muted); font-size: 11px; }}
    .chart-segment {{ stroke-width: 3; stroke-linecap: round; }}
    .chart-dot {{ stroke: var(--panel); stroke-width: 2; }}
    .chart-legend {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px 16px;
      min-height: 18px;
      margin-top: 4px;
    }}
    .legend-item {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      color: var(--muted);
      font-size: 12px;
    }}
    .legend-item i {{
      display: inline-block;
      width: 9px;
      height: 9px;
      border-radius: 50%;
    }}
    .chart-empty {{
      color: var(--muted);
      min-height: 180px;
      display: grid;
      place-items: center;
      text-align: center;
      font-size: 14px;
    }}
    .model-list {{
      display: grid;
      gap: 12px;
    }}
    .model-row {{ padding: 18px; }}
    .model-row-head {{
      display: flex;
      align-items: start;
      justify-content: space-between;
      gap: 18px;
      margin-bottom: 16px;
    }}
    .model-row-head h3 {{
      display: flex;
      align-items: center;
      gap: 9px;
      font-size: 17px;
      letter-spacing: 0;
      margin: 0 0 4px;
      overflow-wrap: anywhere;
    }}
    .model-row-head h3 i {{
      display: inline-block;
      flex: 0 0 auto;
      width: 10px;
      height: 10px;
      border-radius: 50%;
    }}
    .model-row-head p {{ color: var(--muted); font-size: 12px; }}
    .model-row-head > strong {{ white-space: nowrap; font-size: 15px; }}
    .metric-bar-row {{
      display: grid;
      grid-template-columns: minmax(150px, .9fr) minmax(180px, 1.8fr) minmax(145px, 1fr);
      align-items: center;
      gap: 12px;
      margin-top: 10px;
      font-size: 13px;
    }}
    .metric-bar-row > span {{ color: var(--muted); }}
    .metric-bar-row > strong {{ text-align: right; font-size: 12px; }}
    .metric-track {{
      width: 100%;
      height: 10px;
      background: color-mix(in srgb, var(--line), transparent 15%);
      border-radius: 5px;
      overflow: hidden;
    }}
    .metric-track i {{
      display: block;
      height: 100%;
      border-radius: inherit;
    }}
    .metric-track.quota {{ height: 7px; }}
    .model-facts {{
      display: flex;
      flex-wrap: wrap;
      gap: 7px 18px;
      color: var(--muted);
      border-top: 1px solid var(--line);
      padding-top: 12px;
      margin-top: 14px;
      font-size: 12px;
    }}
    .method-note {{
      color: var(--muted);
      font-size: 13px;
      line-height: 1.5;
      border-left: 3px solid var(--accent);
      padding-left: 12px;
      margin-top: 14px;
    }}
    .method-note strong {{ color: var(--ink); }}
    .history-empty h3 {{
      font-size: 16px;
      letter-spacing: 0;
      margin: 0 0 5px;
    }}
    .history-empty p {{ color: var(--muted); font-size: 13px; }}
    @media (max-width: 720px) {{
      body {{ padding: 20px; }}
      header, .summary, .usage-grid, .history-summary {{ grid-template-columns: 1fr; }}
      .section-heading {{ align-items: stretch; flex-direction: column; }}
      .history-controls, .range-control {{ width: 100%; }}
      .history-controls {{ justify-content: space-between; }}
      .history-summary div, .history-summary div:first-child {{
        padding: 11px 0;
        border-right: 0;
        border-bottom: 1px solid var(--line);
      }}
      .history-summary div:last-child {{ border-bottom: 0; }}
      .quota-comparison-heading {{ display: grid; gap: 6px; }}
      .quota-comparison-heading > span {{ white-space: normal; }}
      .quota-cost-header {{ display: none; }}
      .quota-cost-row {{
        grid-template-columns: 1fr;
        gap: 12px;
      }}
      .quota-cost-cell {{ padding-left: 18px; }}
      .quota-cost-cell::before {{
        content: attr(data-label);
        color: var(--muted);
        font-size: 11px;
        font-weight: 750;
        text-transform: uppercase;
      }}
      .metric-definitions dl {{ grid-template-columns: 1fr; gap: 12px; }}
      .timeline-chart svg {{ min-height: 180px; }}
      .model-row-head {{ align-items: start; }}
      .metric-bar-row {{
        grid-template-columns: 1fr auto;
        gap: 7px 10px;
      }}
      .metric-track {{ grid-column: 1 / -1; grid-row: 2; }}
      .metric-bar-row > strong {{ text-align: right; }}
    }}
    @media (max-width: 460px) {{
      body {{ padding: 14px; }}
      .summary-card, .reset-card, .usage-card, .history-panel, .model-row {{ padding: 14px; }}
      .model-row-head {{ display: grid; }}
      .model-row-head > strong {{ white-space: normal; }}
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>Codex usage report</h1>
        <p class="checked">Snapshot generated {html_escape(format_dt_compact(now, local_tz))}</p>
      </div>
      <div class="toolbar">
        <span class="mode">{html_escape(mode_label)}</span>
        {refresh_action}
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
        <p class="label">Countdown from snapshot</p>
        <p class="value">{html_escape(next_left)}</p>
      </div>
    </section>

    <section class="usage-grid">
      {"".join(usage_cards)}
    </section>

    <section class="resets">
      {"".join(cards)}
    </section>

    {model_section}

    <p class="note"><strong>Safety:</strong> this page uses a local read-only checker bound to your machine. Redeem resets only inside the Codex app. {html_escape(refresh_copy)}</p>
  </main>
</body>
</html>
"""


def build_summary_payload(
    reset_payload: dict[str, Any],
    usage_payload: dict[str, Any] | None = None,
    latest_model: dict[str, Any] | None = None,
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
        "model": latest_model,
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


def parse_history_days(value: str) -> int:
    try:
        days = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("history days must be a whole number") from error
    if days < 0:
        raise argparse.ArgumentTypeError("history days cannot be negative")
    return days


class ReusableThreadingTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def serve_dashboard(
    auth_path: Path,
    codex_home: Path,
    history_cache: Path | None,
    history_days: int,
    include_model_history: bool,
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

        def send_json(self, status: int, payload: dict[str, Any]) -> None:
            body_bytes = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body_bytes)))
            self.send_header("Cache-Control", "no-store")
            self.send_header(
                "Content-Disposition",
                'attachment; filename="codex-model-usage-report.json"',
            )
            self.end_headers()
            self.wfile.write(body_bytes)

        def requested_history_days(self, parsed: urllib.parse.ParseResult) -> int:
            values = urllib.parse.parse_qs(parsed.query).get("days") or []
            if not values:
                return history_days
            try:
                requested = int(values[0])
            except (TypeError, ValueError):
                return history_days
            return requested if 0 <= requested <= 3650 else history_days

        def collect_report(self, requested_days: int, now: datetime) -> dict[str, Any]:
            return collect_model_usage_report(
                codex_home=codex_home,
                cache_path=history_cache,
                history_days=requested_days,
                now=now,
            )

        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == "/health":
                self.send_text(200, "ok\n")
                return
            if parsed.path == "/favicon.ico":
                self.send_response(204)
                self.end_headers()
                return
            requested_days = self.requested_history_days(parsed)
            if parsed.path == "/model-usage.json":
                if not include_model_history:
                    self.send_json(404, {"error": "Model history is disabled."})
                    return
                try:
                    self.send_json(
                        200,
                        self.collect_report(requested_days, datetime.now(timezone.utc)),
                    )
                except Exception as error:
                    self.send_json(500, {"error": f"Could not read model history: {error}"})
                return
            if parsed.path not in {"/", "/index.html"}:
                self.send_text(404, "not found\n")
                return

            try:
                now = datetime.now(timezone.utc)
                payload = fetch_reset_payload(auth_path)
                usage_payload = try_fetch_usage_payload(auth_path)
                model_report = None
                if include_model_history:
                    try:
                        model_report = self.collect_report(requested_days, now)
                    except Exception as history_error:
                        model_report = build_model_usage_report(
                            [],
                            now=now,
                            history_days=requested_days,
                            collection_meta={"error": str(history_error)},
                        )
                body = render_html_dashboard(
                    payload,
                    usage_payload=usage_payload,
                    model_report=model_report,
                    now=now,
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
    print(f"Codex usage report is running at {url}", flush=True)
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
        print("\nStopped Codex usage report.")
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
        "--codex-home",
        type=Path,
        default=default_codex_home(),
        help="Codex data directory used for local model/token history. Defaults to $CODEX_HOME or ~/.codex.",
    )
    parser.add_argument(
        "--history-cache",
        type=Path,
        help=f"Private incremental history cache. Defaults to $CODEX_HOME/{HISTORY_CACHE_NAME}.",
    )
    parser.add_argument(
        "--history-days",
        type=parse_history_days,
        default=DEFAULT_HISTORY_DAYS,
        help=f"Model history range in days; 0 means all local history. Default: {DEFAULT_HISTORY_DAYS}.",
    )
    parser.add_argument(
        "--no-model-history",
        action="store_true",
        help="Do not read local Codex rollout records for model/token statistics.",
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
        "--model-json",
        type=Path,
        help="Write a sanitized aggregate model/token usage report as JSON.",
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
            args.codex_home,
            args.history_cache,
            args.history_days,
            not args.no_model_history,
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

    should_print = not args.quiet or (not args.ics and not args.html and not args.model_json)

    if args.summary_json:
        print(
            json.dumps(
                build_summary_payload(
                    payload,
                    usage_payload=usage_payload,
                    latest_model=latest_local_model(args.codex_home),
                ),
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

    generated_at = datetime.now(timezone.utc)
    model_report = None
    model_reports = None
    if not args.no_model_history and (args.html or args.model_json):
        history_ranges = [args.history_days]
        if args.html:
            history_ranges = [7, 30, 90, 0, args.history_days]
        try:
            model_reports = collect_model_usage_reports(
                history_ranges,
                codex_home=args.codex_home,
                cache_path=args.history_cache,
                now=generated_at,
            )
            model_report = model_reports[args.history_days]
        except Exception as history_error:
            if args.model_json:
                print(f"Could not build model usage report: {history_error}", file=sys.stderr)
                return 2
            model_reports = {
                days: build_model_usage_report(
                    [],
                    now=generated_at,
                    history_days=days,
                    collection_meta={"error": str(history_error)},
                )
                for days in dict.fromkeys(history_ranges)
            }
            model_report = model_reports[args.history_days]

    if args.model_json:
        if model_report is None:
            print("Model history is disabled; no JSON report was written.", file=sys.stderr)
            return 2
        args.model_json.expanduser().write_text(
            json.dumps(model_report, indent=2, sort_keys=True) + "\n"
        )
        if should_print:
            print(f"\nWrote model usage JSON: {args.model_json}")

    if args.html:
        args.html.expanduser().write_text(
            render_html_dashboard(
                payload,
                usage_payload=usage_payload,
                model_report=model_report,
                model_reports=model_reports,
                now=generated_at,
                warning_hours=args.warning_hours,
            )
        )
        if should_print:
            print(f"\nWrote HTML dashboard: {args.html}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
