"""Small shared value helpers for the domain and application layers."""

import re
from typing import Final

from doculens.domain.errors import InvalidInputError

# All C0 controls and DEL — including TAB/LF/CR — so labels cannot forge log lines or break UIs.
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")
_COLLAPSE_WHITESPACE = re.compile(r"\s+")


class Unset:
    """Marker for "field not supplied" in partial updates, distinct from ``None``."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "UNSET"


UNSET: Final = Unset()


def clean_label(value: str, *, field: str, max_length: int) -> str:
    """Trim a user-supplied label and enforce non-emptiness, length, and no control characters.

    Control characters in filenames or collection names enable log forging and awkward UI
    rendering; they are refused rather than silently stripped so the client can correct input.
    """
    cleaned = value.strip()
    if not cleaned:
        message = f"The {field} must not be empty."
        raise InvalidInputError(message)
    if _CONTROL_CHARACTERS.search(cleaned) is not None:
        message = f"The {field} must not contain control characters."
        raise InvalidInputError(message)
    if len(cleaned) > max_length:
        message = f"The {field} must be at most {max_length} characters long."
        raise InvalidInputError(message)
    return cleaned


def title_from_text(value: str, *, max_length: int, fallback: str = "New conversation") -> str:
    """Derive a safe display title from free-form text (e.g. the first question).

    Unlike :func:`clean_label`, adversarial questions may contain control characters; those are
    stripped so a conversation can still be created. The raw question is stored separately.
    """
    scrubbed = _COLLAPSE_WHITESPACE.sub(" ", _CONTROL_CHARACTERS.sub(" ", value)).strip()
    if not scrubbed:
        scrubbed = fallback
    return clean_label(scrubbed[:max_length], field="title", max_length=max_length)
