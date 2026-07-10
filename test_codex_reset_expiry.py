#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("codex-reset-expiry.py")
spec = importlib.util.spec_from_file_location("codex_reset_expiry", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = module
spec.loader.exec_module(module)


class CodexResetExpiryTests(unittest.TestCase):
    def sample_payload(self):
        return {
            "available_count": 1,
            "credits": [
                {
                    "title": "One free rate limit reset",
                    "status": "available",
                    "granted_at": "2026-06-18T00:32:04.352158Z",
                    "expires_at": "2026-07-18T00:32:04.352158Z",
                }
            ],
        }

    def sample_usage_payload(self):
        return {
            "rate_limit": {
                "primary_window": {
                    "limit_window_seconds": 18000,
                    "reset_at": 1781821570,
                    "reset_after_seconds": 9750,
                    "used_percent": 82,
                },
                "secondary_window": {
                    "limit_window_seconds": 604800,
                    "reset_at": 1782335935,
                    "reset_after_seconds": 524114,
                    "used_percent": 37,
                },
            }
        }

    def sample_two_reset_payload(self):
        return {
            "available_count": 2,
            "credits": [
                {
                    "title": "One free rate limit reset",
                    "status": "available",
                    "granted_at": "2026-06-18T00:32:04.352158Z",
                    "expires_at": "2026-07-18T00:32:04.352158Z",
                },
                {
                    "title": "One free rate limit reset",
                    "status": "available",
                    "granted_at": "2026-06-24T12:00:00Z",
                    "expires_at": "2026-07-24T12:00:00Z",
                },
            ],
        }

    def history_event(
        self,
        timestamp,
        model,
        total_tokens,
        input_tokens,
        cached_input_tokens,
        output_tokens,
        reasoning_output_tokens,
        primary_used,
        primary_reset,
        secondary_used=40,
        secondary_reset=2000,
    ):
        return {
            "timestamp": timestamp,
            "session": "fixture-session",
            "position": 1,
            "model": model,
            "effort": "high",
            "tokens": {
                "input": input_tokens,
                "cachedInput": cached_input_tokens,
                "output": output_tokens,
                "reasoningOutput": reasoning_output_tokens,
                "total": total_tokens,
            },
            "limitId": "codex",
            "primary": {
                "usedPercent": primary_used,
                "windowMinutes": 300,
                "resetsAt": primary_reset,
            },
            "secondary": {
                "usedPercent": secondary_used,
                "windowMinutes": 10080,
                "resetsAt": secondary_reset,
            },
        }

    def test_report_shows_local_expiry_and_time_left(self):
        report = module.render_report(
            self.sample_payload(),
            now=datetime(2026, 6, 18, 18, 0, tzinfo=timezone.utc),
            local_tz=timezone.utc,
        )

        self.assertIn("Available resets: 1", report)
        self.assertIn("Expires: Jul 18, 2026 at 12:32 AM UTC", report)
        self.assertIn("Time left: 29d 6h", report)
        self.assertIn("redeem resets only in the Codex app", report)

    def test_report_shows_usage_windows_in_human_terms(self):
        report = module.render_report(
            self.sample_payload(),
            usage_payload=self.sample_usage_payload(),
            now=datetime(2026, 6, 18, 18, 0, tzinfo=timezone.utc),
            local_tz=timezone.utc,
        )

        self.assertIn("Usage", report)
        self.assertIn("5 hour: 18% left (82% used)", report)
        self.assertIn("Weekly: 63% left (37% used)", report)
        self.assertNotIn("primary", report.lower())
        self.assertNotIn("secondary", report.lower())

    def test_calendar_export_contains_expiry_event_and_alarms(self):
        calendar = module.build_ics(self.sample_payload(), remind_hours=[24, 6])

        self.assertIn("BEGIN:VCALENDAR", calendar)
        self.assertIn("SUMMARY:Codex reset expires", calendar)
        self.assertIn("DTSTART:20260718T003204Z", calendar)
        self.assertIn("TRIGGER:-PT24H", calendar)
        self.assertIn("TRIGGER:-PT6H", calendar)

    def test_pretty_report_is_clean_and_keeps_safety_copy(self):
        report = module.render_pretty_report(
            self.sample_payload(),
            usage_payload=self.sample_usage_payload(),
            now=datetime(2026, 6, 18, 18, 0, tzinfo=timezone.utc),
            local_tz=timezone.utc,
            color=False,
        )

        self.assertIn("+", report)
        self.assertIn("Codex reset expiry", report)
        self.assertIn("1 available reset", report)
        self.assertIn("Free Codex reset", report)
        self.assertIn("UTC", report)
        self.assertIn("[##################] 98%", report)
        self.assertIn("Usage remaining", report)
        self.assertIn("5 hour", report)
        self.assertIn("18% left", report)
        self.assertIn("Redeem only inside the Codex app", report)
        self.assertNotIn("\033[", report)

    def test_html_dashboard_contains_summary_and_local_safety_copy(self):
        dashboard = module.render_html_dashboard(
            self.sample_payload(),
            usage_payload=self.sample_usage_payload(),
            now=datetime(2026, 6, 18, 18, 0, tzinfo=timezone.utc),
            local_tz=timezone.utc,
        )

        self.assertIn("<!doctype html>", dashboard)
        self.assertIn("Codex usage report", dashboard)
        self.assertIn("1 reset", dashboard)
        self.assertIn("Free Codex reset", dashboard)
        self.assertIn("Jul 18, 2026 at 12:32 AM", dashboard)
        self.assertNotIn("12:32 AM UTC", dashboard)
        self.assertIn("29 days, 6 hours", dashboard)
        self.assertIn("5 hour usage limit", dashboard)
        self.assertIn("18% left", dashboard)
        self.assertIn("Weekly usage limit", dashboard)
        self.assertIn("63% left", dashboard)
        self.assertIn("Redeem resets only inside the Codex app", dashboard)

    def test_live_html_dashboard_contains_refresh_affordance(self):
        dashboard = module.render_html_dashboard(
            self.sample_payload(),
            now=datetime(2026, 6, 18, 18, 0, tzinfo=timezone.utc),
            local_tz=timezone.utc,
            live=True,
            refresh_seconds=300,
        )

        self.assertIn('http-equiv="refresh" content="300"', dashboard)
        self.assertIn("Live local dashboard", dashboard)
        self.assertIn("Auto-refreshes every 300 seconds", dashboard)
        self.assertIn('href="/?days=30"', dashboard)

    def test_support_helpers_format_progress_and_words(self):
        self.assertEqual(module.ascii_bar(50, width=10), "[#####-----]")
        self.assertEqual(module.format_duration_words(3660), "1 hour, 1 minute")
        self.assertEqual(
            module.default_history_cache_path(Path("/tmp")).suffixes[-2:],
            [".json", ".gz"],
        )
        self.assertIn("Could not load Codex reset expiry", module.render_error_html("x"))

    def test_summary_payload_is_sanitized_for_ui_clients(self):
        summary = module.build_summary_payload(
            self.sample_payload(),
            usage_payload=self.sample_usage_payload(),
            now=datetime(2026, 6, 18, 18, 0, tzinfo=timezone.utc),
            local_tz=timezone.utc,
        )

        self.assertEqual(summary["usage"][0]["label"], "5 hour")
        self.assertEqual(summary["usage"][0]["remainingPercent"], 18)
        self.assertEqual(summary["usage"][1]["label"], "Weekly")
        self.assertEqual(summary["resets"]["availableCount"], 1)
        self.assertEqual(summary["resets"]["nextTimeLeft"], "29 days, 6 hours")
        self.assertEqual(summary["resets"]["items"][0]["number"], 1)
        self.assertEqual(summary["resets"]["items"][0]["expires"], "Jul 18, 2026 at 12:32 AM")
        self.assertEqual(
            summary["resets"]["items"][0]["expiresUTC"],
            "Jul 18, 2026 at 12:32 AM UTC",
        )
        self.assertEqual(summary["resets"]["items"][0]["timeLeft"], "29 days, 6 hours")
        self.assertNotIn("UTC", summary["checkedAt"])
        self.assertNotIn("UTC", summary["usage"][0]["resetsAt"])
        self.assertNotIn("access_token", str(summary))

    def test_latest_model_comes_from_read_only_local_state(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "state_5.sqlite"
            connection = sqlite3.connect(database)
            connection.execute(
                "CREATE TABLE threads (model TEXT, reasoning_effort TEXT, recency_at_ms INTEGER, updated_at_ms INTEGER)"
            )
            connection.execute(
                "INSERT INTO threads VALUES (?, ?, ?, ?)",
                ("gpt-5.6-sol", "ultra", 20, 20),
            )
            connection.execute(
                "INSERT INTO threads VALUES (?, ?, ?, ?)",
                ("gpt-5.5", "xhigh", 10, 10),
            )
            connection.commit()
            connection.close()

            latest = module.latest_local_model(Path(directory))

        self.assertEqual(latest, {"name": "gpt-5.6-sol", "effort": "ultra"})

    def test_summary_payload_lists_each_available_reset(self):
        summary = module.build_summary_payload(
            self.sample_two_reset_payload(),
            now=datetime(2026, 6, 18, 18, 0, tzinfo=timezone.utc),
            local_tz=timezone.utc,
        )

        items = summary["resets"]["items"]
        self.assertEqual(summary["resets"]["availableCount"], 2)
        self.assertEqual([item["number"] for item in items], [1, 2])
        self.assertEqual(items[0]["expires"], "Jul 18, 2026 at 12:32 AM")
        self.assertEqual(items[0]["timeLeft"], "29 days, 6 hours")
        self.assertEqual(items[1]["expires"], "Jul 24, 2026 at 12:00 PM")
        self.assertEqual(items[1]["timeLeft"], "35 days, 18 hours")

    def test_rollout_scanner_extracts_only_model_token_and_quota_data(self):
        rows = [
            {
                "timestamp": "2026-07-08T12:00:00Z",
                "type": "session_meta",
                "payload": {"id": "fixture-session", "base_instructions": "PRIVATE"},
            },
            {
                "timestamp": "2026-07-08T12:00:01Z",
                "type": "turn_context",
                "payload": {"model": "gpt-5.5", "effort": "xhigh"},
            },
            {
                "timestamp": "2026-07-08T12:00:02Z",
                "type": "event_msg",
                "payload": {"type": "user_message", "message": "DO NOT RETAIN ME"},
            },
            {
                "timestamp": "2026-07-08T12:00:03Z",
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "last_token_usage": {
                            "input_tokens": 1200,
                            "cached_input_tokens": 800,
                            "output_tokens": 300,
                            "reasoning_output_tokens": 125,
                            "total_tokens": 1500,
                        }
                    },
                    "rate_limits": {
                        "limit_id": "codex",
                        "primary": {
                            "used_percent": 12.0,
                            "window_minutes": 300,
                            "resets_at": 3000,
                        },
                        "secondary": {
                            "used_percent": 34.0,
                            "window_minutes": 10080,
                            "resets_at": 4000,
                        },
                    },
                },
            },
            {
                "timestamp": "2026-07-08T12:01:00Z",
                "type": "turn_context",
                "payload": {"model": "gpt-5.6-codex", "effort": "high"},
            },
            {
                "timestamp": "2026-07-08T12:01:01Z",
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "last_token_usage": {
                            "input_tokens": 2000,
                            "cached_input_tokens": 1500,
                            "output_tokens": 500,
                            "reasoning_output_tokens": 250,
                            "total_tokens": 2500,
                        }
                    },
                    "rate_limits": {
                        "limit_id": "codex",
                        "primary": {
                            "used_percent": 13.0,
                            "window_minutes": 300,
                            "resets_at": 3000,
                        },
                        "secondary": {
                            "used_percent": 34.0,
                            "window_minutes": 10080,
                            "resets_at": 4000,
                        },
                    },
                },
            },
        ]

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rollout-fixture.jsonl"
            path.write_text("".join(json.dumps(row) + "\n" for row in rows))
            events, state = module.scan_rollout_file(path, "fixture-session")

        self.assertEqual([event["model"] for event in events], ["gpt-5.5", "gpt-5.6-codex"])
        self.assertEqual(events[0]["effort"], "xhigh")
        self.assertEqual(events[0]["tokens"]["total"], 1500)
        self.assertEqual(events[0]["primary"]["usedPercent"], 12.0)
        self.assertEqual(events[1]["tokens"]["reasoningOutput"], 250)
        self.assertEqual(state["model"], "gpt-5.6-codex")
        self.assertNotIn("PRIVATE", str(events))
        self.assertNotIn("DO NOT RETAIN ME", str(events))

    def test_history_cache_is_incremental_and_does_not_duplicate_events(self):
        first_rows = [
            {
                "timestamp": "2026-07-08T12:00:01Z",
                "type": "turn_context",
                "payload": {"model": "gpt-5.5", "effort": "high"},
            },
            {
                "timestamp": "2026-07-08T12:00:02Z",
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {"last_token_usage": {"total_tokens": 100}},
                    "rate_limits": None,
                },
            },
        ]
        appended_row = {
            "timestamp": "2026-07-08T12:01:02Z",
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {"last_token_usage": {"total_tokens": 200}},
                "rate_limits": None,
            },
        }

        with tempfile.TemporaryDirectory() as directory:
            codex_home = Path(directory)
            session_dir = codex_home / "sessions" / "2026" / "07" / "08"
            session_dir.mkdir(parents=True)
            rollout = session_dir / "rollout-fixture.jsonl"
            rollout.write_text("".join(json.dumps(row) + "\n" for row in first_rows))
            cache_path = codex_home / "usage-history.json"

            first, first_meta = module.collect_model_history(codex_home, cache_path)
            second, second_meta = module.collect_model_history(codex_home, cache_path)
            with rollout.open("a") as handle:
                handle.write(json.dumps(appended_row) + "\n")
            third, third_meta = module.collect_model_history(codex_home, cache_path)

            self.assertEqual(len(first), 1)
            self.assertEqual(len(second), 1)
            self.assertEqual(len(third), 2)
            self.assertEqual(third[-1]["model"], "gpt-5.5")
            self.assertEqual(first_meta["newEvents"], 1)
            self.assertEqual(second_meta["newEvents"], 0)
            self.assertEqual(third_meta["newEvents"], 1)
            self.assertEqual(cache_path.stat().st_mode & 0o777, 0o600)

    def test_model_report_attributes_positive_deltas_without_counting_window_resets(self):
        events = [
            self.history_event(
                "2026-07-08T12:00:00Z", "gpt-5.5", 100_000, 90_000, 60_000, 10_000, 4_000, 10, 1000
            ),
            self.history_event(
                "2026-07-08T12:05:00Z", "gpt-5.5", 200_000, 180_000, 120_000, 20_000, 8_000, 12, 1000
            ),
            self.history_event(
                "2026-07-08T12:10:00Z", "gpt-5.6-codex", 100_000, 90_000, 45_000, 10_000, 3_000, 15, 1000
            ),
            self.history_event(
                "2026-07-08T17:00:00Z", "gpt-5.6-codex", 100_000, 90_000, 45_000, 10_000, 3_000, 1, 3000
            ),
            self.history_event(
                "2026-07-08T17:05:00Z", "gpt-5.6-codex", 100_000, 90_000, 45_000, 10_000, 3_000, 2, 3000
            ),
        ]

        report = module.build_model_usage_report(
            events,
            now=datetime(2026, 7, 9, tzinfo=timezone.utc),
            history_days=30,
        )
        by_model = {item["model"]: item for item in report["models"]}

        self.assertEqual(by_model["gpt-5.5"]["totalTokens"], 300_000)
        self.assertEqual(by_model["gpt-5.5"]["primaryDelta"], 2.0)
        self.assertAlmostEqual(by_model["gpt-5.5"]["primaryPerMillion"], 6.6667, places=3)
        self.assertEqual(by_model["gpt-5.6-codex"]["totalTokens"], 300_000)
        self.assertEqual(by_model["gpt-5.6-codex"]["primaryDelta"], 4.0)
        self.assertAlmostEqual(by_model["gpt-5.6-codex"]["primaryPerMillion"], 13.3333, places=3)
        self.assertEqual(report["currentModel"], "gpt-5.6-codex")
        self.assertNotEqual(by_model["gpt-5.5"]["color"], by_model["gpt-5.6-codex"]["color"])
        self.assertEqual(
            [point["usedPercent"] for point in report["timeline"]],
            [10.0, 12.0, 15.0, 1.0, 2.0],
        )
        self.assertEqual(report["timelineDays"], 7)
        self.assertEqual(report["localHistoryDays"], 1)
        self.assertEqual(report["localHistoryStart"], "2026-07-08T12:00:00+00:00")
        self.assertEqual(report["knownModelTokenPercent"], 100.0)

    def test_html_dashboard_graphs_model_usage_and_explains_estimate(self):
        model_report = module.build_model_usage_report(
            [
                self.history_event(
                    "2026-07-08T12:00:00Z", "gpt-5.5", 100_000, 90_000, 60_000, 10_000, 4_000, 10, 1000
                ),
                self.history_event(
                    "2026-07-08T12:05:00Z", "gpt-5.6-codex", 100_000, 90_000, 45_000, 10_000, 3_000, 12, 1000
                ),
            ],
            now=datetime(2026, 7, 9, tzinfo=timezone.utc),
            history_days=30,
        )
        dashboard = module.render_html_dashboard(
            self.sample_payload(),
            usage_payload=self.sample_usage_payload(),
            model_report=model_report,
            now=datetime(2026, 7, 9, tzinfo=timezone.utc),
            local_tz=timezone.utc,
        )

        self.assertIn("Model usage", dashboard)
        self.assertIn("gpt-5.6-codex", dashboard)
        self.assertIn("Recorded token volume", dashboard)
        self.assertIn("5h quota burn / 1M tokens", dashboard)
        self.assertIn("Metric definitions", dashboard)
        self.assertIn("Quota trajectory", dashboard)
        self.assertIn("<svg", dashboard)
        self.assertIn("Export JSON", dashboard)
        self.assertIn('download="codex-model-usage-report.json"', dashboard)
        self.assertIn("rounded, account-wide", dashboard)
        self.assertIn("Actual token counts", dashboard)

    def test_static_dashboard_embeds_clickable_history_ranges(self):
        events = [
            self.history_event(
                "2026-07-08T12:00:00Z", "gpt-5.5", 100_000, 90_000, 60_000, 10_000, 4_000, 10, 1000
            )
        ]
        reports = {
            days: module.build_model_usage_report(
                events,
                now=datetime(2026, 7, 9, tzinfo=timezone.utc),
                history_days=days,
            )
            for days in (7, 30, 90, 0)
        }
        dashboard = module.render_html_dashboard(
            self.sample_payload(),
            model_report=reports[30],
            model_reports=reports,
            now=datetime(2026, 7, 9, tzinfo=timezone.utc),
            local_tz=timezone.utc,
        )

        self.assertIn('data-history-panel="7"', dashboard)
        self.assertIn('data-history-panel="30"', dashboard)
        self.assertIn('data-history-panel="90"', dashboard)
        self.assertIn('data-history-panel="0"', dashboard)
        self.assertIn('data-history-range="7"', dashboard)
        self.assertIn('data-history-range="90"', dashboard)
        self.assertIn('data-history-range="0"', dashboard)
        self.assertIn("history.replaceState", dashboard)
        self.assertIn("days available locally", dashboard)

    def test_history_ranges_apply_distinct_local_cutoffs(self):
        events = [
            self.history_event(
                "2026-04-20T12:00:00Z", "gpt-5.2", 100_000, 90_000, 60_000, 10_000, 4_000, 10, 1000
            ),
            self.history_event(
                "2026-06-20T12:00:00Z", "gpt-5.5", 200_000, 180_000, 120_000, 20_000, 8_000, 20, 2000
            ),
            self.history_event(
                "2026-07-08T12:00:00Z", "gpt-5.6-codex", 300_000, 270_000, 180_000, 30_000, 12_000, 30, 3000
            ),
        ]
        now = datetime(2026, 7, 9, tzinfo=timezone.utc)
        reports = {
            days: module.build_model_usage_report(events, now=now, history_days=days)
            for days in (7, 30, 90, 0)
        }

        self.assertEqual(reports[7]["totalTokens"], 300_000)
        self.assertEqual(reports[30]["totalTokens"], 500_000)
        self.assertEqual(reports[90]["totalTokens"], 600_000)
        self.assertEqual(reports[0]["totalTokens"], reports[90]["totalTokens"])
        self.assertEqual(reports[90]["localHistoryDays"], reports[0]["localHistoryDays"])

    def test_production_script_has_no_mutating_reset_path(self):
        source = MODULE_PATH.read_text()

        self.assertIn("rate-limit-reset-credits", source)
        self.assertNotIn("rate-limit-reset-credits/consume", source)
        self.assertNotIn("redeem_request_id", source)
        self.assertNotIn(".post(", source)


if __name__ == "__main__":
    unittest.main()
