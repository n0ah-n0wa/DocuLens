from dataclasses import replace
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


def test_a_ready_document_can_be_reindexed_or_reprocessed_or_deleted() -> None:
    ready = _document(ProcessingStatus.READY)
    assert ready.transition_to(ProcessingStatus.CHUNKING, now=T1).processing_status is (
        ProcessingStatus.CHUNKING
    )
    assert ready.transition_to(ProcessingStatus.VALIDATING, now=T1).processing_status is (
        ProcessingStatus.VALIDATING
    )
    assert ready.transition_to(ProcessingStatus.DELETING, now=T1).processing_status is (
        ProcessingStatus.DELETING
    )


def test_as_deleted_clears_derived_content_and_keeps_identity() -> None:
    deleting = replace(
        _document(ProcessingStatus.DELETING),
        page_count=4,
        chunk_count=12,
        indexed_at=T0,
        processing_error="stale",
    )
    tombstone = deleting.as_deleted(now=T1)

    assert tombstone.processing_status is ProcessingStatus.DELETED
    assert tombstone.page_count is None
    assert tombstone.chunk_count == 0
    assert tombstone.indexed_at is None
    assert tombstone.processing_error is None
    assert tombstone.id == deleting.id
    assert tombstone.storage_key == deleting.storage_key


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


@pytest.mark.parametrize("source", list(ProcessingStatus))
def test_every_transition_outside_the_table_is_refused(source: ProcessingStatus) -> None:
    document = _document(source)
    for target in ProcessingStatus:
        if target in ALLOWED_TRANSITIONS[source]:
            assert document.transition_to(target, now=T1).processing_status is target
        else:
            with pytest.raises(InvalidStatusTransitionError):
                document.transition_to(target, now=T1)


def test_extraction_facts_are_recorded_without_changing_the_state() -> None:
    document = _document(ProcessingStatus.EXTRACTING)

    updated = document.with_extraction(page_count=4, metadata={"pdf": {"title": "T"}}, now=T1)

    assert updated.page_count == 4
    assert updated.metadata == {"pdf": {"title": "T"}}
    assert updated.processing_status is ProcessingStatus.EXTRACTING
    assert updated.updated_at == T1
    assert document.metadata == {}
