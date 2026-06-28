#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import sys
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
        self.assertIn("Codex reset expiry", dashboard)
        self.assertIn("1 reset", dashboard)
        self.assertIn("Free Codex reset", dashboard)
        self.assertIn("Jul 18, 2026 at 12:32 AM UTC", dashboard)
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
        self.assertIn('href="/"', dashboard)

    def test_support_helpers_format_progress_and_words(self):
        self.assertEqual(module.ascii_bar(50, width=10), "[#####-----]")
        self.assertEqual(module.format_duration_words(3660), "1 hour, 1 minute")
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

    def test_production_script_has_no_mutating_reset_path(self):
        source = MODULE_PATH.read_text()

        self.assertIn("rate-limit-reset-credits", source)
        self.assertNotIn("rate-limit-reset-credits/consume", source)
        self.assertNotIn("redeem_request_id", source)
        self.assertNotIn(".post(", source)


if __name__ == "__main__":
    unittest.main()
