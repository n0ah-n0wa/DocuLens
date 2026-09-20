"""Vector metadata, records, filters, collection naming and the chunk-to-record mapping."""

from uuid import UUID, uuid4

import pytest

from doculens.application.vectors import records_for_chunks
from doculens.domain.chunking import chunk_id
from doculens.domain.documents import DocumentChunk
from doculens.domain.embeddings import EmbeddingResult, EmbeddingUsage
from doculens.domain.vectors import (
    InvalidVectorRecordError,
    SearchFilter,
    VectorDimensionMismatchError,
    VectorMetadata,
    VectorRecord,
    collection_name,
    cosine_similarity,
    vector_id_for,
)

pytestmark = pytest.mark.unit

OWNER = UUID("11111111-1111-4111-8111-111111111111")
DOCUMENT = UUID("22222222-2222-4222-8222-222222222222")


def metadata(**overrides: object) -> VectorMetadata:
    values: dict[str, object] = {
        "user_id": OWNER,
        "document_id": DOCUMENT,
        "chunk_id": chunk_id(DOCUMENT, 3),
        "chunk_index": 3,
        "page_number": 2,
    }
    values.update(overrides)
    return VectorMetadata(**values)  # type: ignore[arg-type]


def test_metadata_carries_the_section_16_fields_and_round_trips_without_nulls() -> None:
    full = metadata(
        collection_id=uuid4(), embedding_model="m", embedding_provider="p", content_hash="h"
    )
    minimal = metadata()

    assert set(full.as_mapping()) == {
        "user_id",
        "document_id",
        "chunk_id",
        "chunk_index",
        "page_number",
        "collection_id",
        "embedding_model",
        "embedding_provider",
        "content_hash",
    }
    assert set(minimal.as_mapping()) == {
        "user_id",
        "document_id",
        "chunk_id",
        "chunk_index",
        "page_number",
        "collection_id",
    }
    assert minimal.as_mapping()["collection_id"] == ""  # cleared, never null or omitted
    assert None not in minimal.as_mapping().values()
    assert VectorMetadata.from_mapping(full.as_mapping()) == full
    assert VectorMetadata.from_mapping(minimal.as_mapping()) == minimal
    assert VectorMetadata.from_mapping({**minimal.as_mapping(), "chunk_index": "3"}) == minimal


def test_malformed_metadata_is_refused() -> None:
    with pytest.raises(InvalidVectorRecordError):
        VectorMetadata.from_mapping({"user_id": "nope"})
    with pytest.raises(InvalidVectorRecordError):
        VectorMetadata.from_mapping({**metadata().as_mapping(), "page_number": "two"})


def test_a_record_is_identified_by_its_chunk_and_must_hold_a_finite_vector() -> None:
    chunk = chunk_id(DOCUMENT, 3)
    assert vector_id_for(chunk) == str(chunk)

    VectorRecord(id=str(chunk), vector=(0.1, 0.2), metadata=metadata())
    with pytest.raises(InvalidVectorRecordError):
        VectorRecord(id="something-else", vector=(0.1,), metadata=metadata())
    with pytest.raises(InvalidVectorRecordError):
        VectorRecord(id=str(chunk), vector=(), metadata=metadata())
    with pytest.raises(InvalidVectorRecordError):
        VectorRecord(id=str(chunk), vector=(float("nan"),), metadata=metadata())


def test_filters_always_require_the_owner_and_narrow_within_it() -> None:
    other = uuid4()
    collection = uuid4()
    mine = metadata(collection_id=collection)

    assert SearchFilter(owner_id=OWNER).matches(mine)
    assert not SearchFilter(owner_id=other).matches(mine)
    assert SearchFilter(owner_id=OWNER, document_ids=(DOCUMENT,)).matches(mine)
    assert not SearchFilter(owner_id=OWNER, document_ids=(uuid4(),)).matches(mine)
    assert SearchFilter(owner_id=OWNER, collection_id=collection).matches(mine)
    assert not SearchFilter(owner_id=OWNER, collection_id=uuid4()).matches(mine)
    assert not SearchFilter(owner_id=OWNER, collection_id=collection).matches(metadata())


def test_collection_names_follow_the_embedding_model() -> None:
    assert (
        collection_name("doculens", "text-embedding-3-small") == "doculens-text-embedding-3-small"
    )
    assert collection_name("doculens", "Nomic/Embed Text v1.5") == "doculens-nomic-embed-text-v1-5"
    assert collection_name("doculens", "m") != collection_name("doculens", "n")
    with pytest.raises(InvalidVectorRecordError):
        collection_name("doculens", "-")


def test_cosine_similarity_is_bounded_and_dimension_checked() -> None:
    assert cosine_similarity((1.0, 0.0), (1.0, 0.0)) == pytest.approx(1.0)
    assert cosine_similarity((1.0, 0.0), (0.0, 1.0)) == pytest.approx(0.0)
    assert cosine_similarity((1.0, 0.0), (-1.0, 0.0)) == pytest.approx(-1.0)
    assert cosine_similarity((0.0, 0.0), (1.0, 0.0)) == 0.0
    with pytest.raises(VectorDimensionMismatchError):
        cosine_similarity((1.0,), (1.0, 0.0))


def test_chunks_and_embeddings_are_paired_into_records_with_full_provenance() -> None:
    collection = uuid4()
    chunks = [
        DocumentChunk(
            id=chunk_id(DOCUMENT, index),
            document_id=DOCUMENT,
            page_id=uuid4(),
            chunk_index=index,
            text=f"text {index}",
            token_count=2,
            metadata={"page_number": index + 1, "content_hash": f"h{index}"},
        )
        for index in range(2)
    ]
    embeddings = EmbeddingResult(
        vectors=((1.0, 0.0), (0.0, 1.0)),
        model="text-embedding-test",
        dimensions=2,
        usage=EmbeddingUsage(requests=1, tokens=4),
    )

    records = records_for_chunks(
        chunks, embeddings, owner_id=OWNER, collection_id=collection, embedding_provider="fake"
    )

    assert [record.id for record in records] == [str(chunk.id) for chunk in chunks]
    assert records[1].vector == (0.0, 1.0)
    assert records[1].text == "text 1"
    assert records[1].metadata == VectorMetadata(
        user_id=OWNER,
        document_id=DOCUMENT,
        chunk_id=chunks[1].id,
        chunk_index=1,
        page_number=2,
        collection_id=collection,
        embedding_model="text-embedding-test",
        embedding_provider="fake",
        content_hash="h1",
    )
    with pytest.raises(InvalidVectorRecordError):
        records_for_chunks(
            chunks[:1], embeddings, owner_id=OWNER, collection_id=None, embedding_provider="fake"
        )
