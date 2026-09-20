from datetime import UTC, datetime
from uuid import uuid4

import pytest

from doculens.domain.documents import (
    ALLOWED_TRANSITIONS,
    Document,
    InvalidStatusTransitionError,
    ProcessingStatus,
)

pytestmark = pytest.mark.unit

T0 = datetime(2026, 1, 1, tzinfo=UTC)
T1 = datetime(2026, 1, 2, tzinfo=UTC)


def _document(status: ProcessingStatus) -> Document:
    return Document(
        id=uuid4(),
        owner_id=uuid4(),
        collection_id=None,
        filename="report.pdf",
        storage_key="documents/u/d/original.pdf",
        content_hash="a" * 64,
        mime_type="application/pdf",
        file_size=1234,
        processing_status=status,
        created_at=T0,
        updated_at=T0,
    )


def test_every_state_of_section_7_3_has_a_transition_rule() -> None:
    assert set(ALLOWED_TRANSITIONS) == set(ProcessingStatus)


def test_the_happy_path_walks_the_pipeline_to_ready() -> None:
    document = _document(ProcessingStatus.UPLOADED)
    for status in (
        ProcessingStatus.VALIDATING,
        ProcessingStatus.EXTRACTING,
        ProcessingStatus.CHUNKING,
        ProcessingStatus.EMBEDDING,
        ProcessingStatus.INDEXING,
        ProcessingStatus.READY,
    ):
        document = document.transition_to(status, now=T1)

    assert document.is_ready
    assert document.updated_at == T1
    assert document.processing_error is None


def test_skipping_a_stage_is_refused() -> None:
    with pytest.raises(InvalidStatusTransitionError) as excinfo:
        _document(ProcessingStatus.UPLOADED).transition_to(ProcessingStatus.READY, now=T1)

    assert excinfo.value.code == "INVALID_STATUS_TRANSITION"


def test_deleted_is_terminal() -> None:
    assert ALLOWED_TRANSITIONS[ProcessingStatus.DELETED] == frozenset()


def test_mark_failed_records_a_safe_message_and_reprocessing_clears_it() -> None:
    failed = _document(ProcessingStatus.EXTRACTING).mark_failed("The PDF is corrupted.", now=T1)

    assert failed.processing_status is ProcessingStatus.FAILED
    assert failed.processing_error == "The PDF is corrupted."

    retried = failed.transition_to(ProcessingStatus.VALIDATING, now=T1)

    assert retried.processing_error is None


def test_entities_are_immutable() -> None:
    document = _document(ProcessingStatus.UPLOADED)

    with pytest.raises(AttributeError):
        document.filename = "other.pdf"  # type: ignore[misc]  # immutability is the point
