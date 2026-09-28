"""Regression tests for label sanitisation (control characters → InvalidInputError)."""

import pytest

from doculens.domain.common import clean_label, title_from_text
from doculens.domain.errors import InvalidInputError

pytestmark = pytest.mark.unit


def test_clean_label_accepts_ordinary_text() -> None:
    assert clean_label("  Q3 report  ", field="filename", max_length=100) == "Q3 report"


@pytest.mark.parametrize(
    "raw",
    [
        "evil\x00name",
        "line\nbreak",
        "tab\there",
        "bell\x07",
        "del\x7f",
    ],
)
def test_clean_label_rejects_control_characters(raw: str) -> None:
    with pytest.raises(InvalidInputError, match="control characters"):
        clean_label(raw, field="name", max_length=100)


def test_title_from_text_strips_controls_and_collapses_whitespace() -> None:
    assert (
        title_from_text("What is leave?\x00\x1b[2J\nNext line", max_length=100)
        == "What is leave? [2J Next line"
    )
    assert title_from_text("\x00\x1f", max_length=100) == "New conversation"
