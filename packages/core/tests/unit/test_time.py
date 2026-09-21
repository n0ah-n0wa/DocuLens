"""The time source is UTC and strictly increasing within the process."""

from datetime import UTC
from itertools import pairwise

import pytest

from doculens.domain.time import utc_now

pytestmark = pytest.mark.unit


def test_timestamps_are_aware_utc_and_strictly_increasing() -> None:
    stamps = [utc_now() for _ in range(10_000)]

    assert all(stamp.tzinfo is UTC for stamp in stamps)
    assert all(later > earlier for earlier, later in pairwise(stamps))
    assert len(set(stamps)) == len(stamps)
