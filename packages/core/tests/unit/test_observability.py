from uuid import uuid4

import pytest
import structlog

from doculens.application.observability import bind_correlation, correlation_context

pytestmark = pytest.mark.unit


def test_bind_correlation_merges_safe_identifiers() -> None:
    structlog.contextvars.clear_contextvars()
    user_id = uuid4()
    document_id = uuid4()

    bound = bind_correlation(
        request_id="req-42",
        user_id=user_id,
        document_id=document_id,
        conversation_id=None,
        job_id="job-7",
    )

    assert bound == {
        "request_id": "req-42",
        "user_id": str(user_id),
        "document_id": str(document_id),
        "job_id": "job-7",
    }
    assert structlog.contextvars.get_contextvars() == bound
    structlog.contextvars.clear_contextvars()


def test_correlation_context_restores_prior_bindings() -> None:
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id="outer")
    conversation_id = uuid4()

    with correlation_context(conversation_id=conversation_id) as bound:
        assert bound == {"conversation_id": str(conversation_id)}
        assert structlog.contextvars.get_contextvars()["request_id"] == "outer"
        assert structlog.contextvars.get_contextvars()["conversation_id"] == str(conversation_id)

    assert structlog.contextvars.get_contextvars() == {"request_id": "outer"}
    structlog.contextvars.clear_contextvars()


def test_correlation_context_ignores_unset_fields() -> None:
    structlog.contextvars.clear_contextvars()
    with correlation_context(request_id="only") as bound:
        assert bound == {"request_id": "only"}
    assert structlog.contextvars.get_contextvars() == {}
