# Changelog

All notable changes to this project are documented here.

## 0.2.0 - 2026-07-09

### Added

- Local per-model token history from Codex rollout records
- Private incremental gzip cache with `0600` permissions
- 7-day, 30-day, 90-day, and all-history report views
- Five-hour quota trajectory with model-colored points and reset gaps
- Per-model cached-input share, response count, and observed quota-burn rate
- Sanitized model-usage JSON export
- Latest model and reasoning effort in the menu-bar menu
- Metric glossary and methodology documentation
- GitHub Actions checks and repository screenshots

### Changed

- Renamed the menu action to **Open Usage Report**
- Removed repeated local timezone suffixes from browser report dates
- Labeled reset countdowns as relative to the snapshot time
- Compressed the local history cache to reduce disk usage

### Security

- History collection retains no prompt, message, task, repository, or tool-output text
- Reset redemption remains intentionally unsupported

## 0.1.0 - 2026-06-28

- Initial menu-bar app, terminal report, HTML dashboard, reset-expiry reminders, and SwiftBar fallback
