"""Time source. All timestamps are timezone-aware UTC (SPECIFICATIONS.md §45, §50).

``utc_now`` is strictly increasing within a process: two calls never return the same instant,
even on platforms whose wall clock ticks coarsely. Records that are created in quick succession
(a conversation's turns, a question and its answer) therefore keep their order when repositories
sort by timestamp, and a rename always moves a row ahead of rows created in the same tick.
"""

import threading
from datetime import UTC, datetime, timedelta

_lock = threading.Lock()
_last: datetime = datetime.min.replace(tzinfo=UTC)
_TICK = timedelta(microseconds=1)


def utc_now() -> datetime:
    global _last  # noqa: PLW0603 - the monotonic guard is process-wide by design
    now = datetime.now(UTC)
    with _lock:
        if now <= _last:
            now = _last + _TICK
        _last = now
    return now
