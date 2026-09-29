"""Correlation helpers for structured logs (SPECIFICATIONS.md §50).

Binds safe identifiers into structlog contextvars so every log line in the same request or job
carries ``request_id`` / ``user_id`` / ``document_id`` / ``conversation_id`` without handlers
repeating them. Never bind secrets or document text.
"""

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from uuid import UUID

import structlog


def bind_correlation(
    *,
    request_id: str | None = None,
    user_id: UUID | str | None = None,
    document_id: UUID | str | None = None,
    conversation_id: UUID | str | None = None,
    job_id: str | None = None,
) -> Mapping[str, str]:
    """Bind safe correlation identifiers into the logging context; returns what was bound."""
    bound: dict[str, str] = {}
    if request_id:
        bound["request_id"] = request_id
    if user_id is not None:
        bound["user_id"] = str(user_id)
    if document_id is not None:
        bound["document_id"] = str(document_id)
    if conversation_id is not None:
        bound["conversation_id"] = str(conversation_id)
    if job_id:
        bound["job_id"] = job_id
    if bound:
        structlog.contextvars.bind_contextvars(**bound)
    return bound


@contextmanager
def correlation_context(
    *,
    request_id: str | None = None,
    user_id: UUID | str | None = None,
    document_id: UUID | str | None = None,
    conversation_id: UUID | str | None = None,
    job_id: str | None = None,
) -> Iterator[Mapping[str, str]]:
    """Scoped correlation binding that restores prior contextvars on exit."""
    normalised: dict[str, str] = {}
    if request_id:
        normalised["request_id"] = request_id
    if user_id is not None:
        normalised["user_id"] = str(user_id)
    if document_id is not None:
        normalised["document_id"] = str(document_id)
    if conversation_id is not None:
        normalised["conversation_id"] = str(conversation_id)
    if job_id:
        normalised["job_id"] = job_id
    with structlog.contextvars.bound_contextvars(**normalised):
        yield normalised


__all__ = ["bind_correlation", "correlation_context"]
