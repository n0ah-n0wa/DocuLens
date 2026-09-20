"""Time source. All timestamps are timezone-aware UTC (SPECIFICATIONS.md §45, §50)."""

from datetime import UTC, datetime


def utc_now() -> datetime:
    return datetime.now(UTC)
