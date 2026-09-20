"""Small shared value helpers for the domain and application layers."""

from typing import Final

from doculens.domain.errors import InvalidInputError


class Unset:
    """Marker for "field not supplied" in partial updates, distinct from ``None``."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "UNSET"


UNSET: Final = Unset()


def clean_label(value: str, *, field: str, max_length: int) -> str:
    """Trim a user-supplied label and enforce non-emptiness and length (§53: validate input)."""
    cleaned = value.strip()
    if not cleaned:
        message = f"The {field} must not be empty."
        raise InvalidInputError(message)
    if len(cleaned) > max_length:
        message = f"The {field} must be at most {max_length} characters long."
        raise InvalidInputError(message)
    return cleaned
