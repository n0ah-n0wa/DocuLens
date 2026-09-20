"""The ChromaDB adapter against a real server (testcontainers, or ``DOCULENS_TEST_CHROMA_URL``).

Runs the shared vector-store contract (insert, query, filters, delete, re-index, duplicates,
cross-user isolation) plus adapter-specific checks: collection naming and metadata, readiness,
and the unreachable-server error.
"""

import os
from collections.abc import AsyncIterator, Iterator
from uuid import uuid4

import pytest
from pydantic import SecretStr

from doculens.domain.vectors import SearchFilter, VectorStoreUnavailableError
from doculens.infrastructure.config import CoreSettings, VectorStoreKind
from doculens.infrastructure.vectors import ChromaVectorStore, VectorStoreProbe, build_vector_store
from doculens.testing.chroma import VectorStoreUnavailableForTestsError, provisioned_chroma_url
from doculens.testing.vector_contract import VectorStoreContract, axis, record

pytestmark = pytest.mark.integration

DB_URL = SecretStr("postgresql+asyncpg://u:p@127.0.0.1:1/doculens")


@pytest.fixture(scope="session")
def chroma_url() -> Iterator[str]:
    try:
        with provisioned_chroma_url() as url:
            yield url
    except VectorStoreUnavailableForTestsError as exc:
        if os.environ.get("CI"):
            raise
        pytest.skip(str(exc))


@pytest.fixture
async def store(chroma_url: str) -> AsyncIterator[ChromaVectorStore]:
    """A fresh, uniquely named collection per test; dropped afterwards."""
    subject = ChromaVectorStore.from_url(
        chroma_url,
        collection=f"doculens-test-{uuid4().hex[:12]}",
        collection_metadata={"embedding_model": "fake-embedding-v1"},
    )
    await subject.ensure_collection()
    try:
        yield subject
    finally:
        await subject.drop_collection()


class TestChromaVectorStore(VectorStoreContract):
    pass


async def test_the_collection_records_its_embedding_model(store: ChromaVectorStore) -> None:
    info = await store.ensure_collection()

    assert info.name == store.collection
    assert info.metadata["embedding_model"] == "fake-embedding-v1"
    assert info.count == 0


async def test_readiness_passes_against_the_live_server(store: ChromaVectorStore) -> None:
    probe = VectorStoreProbe(store)
    assert probe.name == "vector-store"

    await probe.check()


async def test_an_unreachable_server_is_reported_as_unavailable() -> None:
    unreachable = ChromaVectorStore.from_url("http://127.0.0.1:1", collection="doculens-nowhere")

    with pytest.raises(VectorStoreUnavailableError):
        await unreachable.check()
    with pytest.raises(VectorStoreUnavailableError):
        await unreachable.search(axis(0), scope=SearchFilter(owner_id=uuid4()), limit=1)


async def test_the_factory_builds_a_chroma_store_from_settings(chroma_url: str) -> None:
    settings = CoreSettings(
        _env_file=None,
        database_url=DB_URL,
        vector_store=VectorStoreKind.CHROMA,
        chroma_url=chroma_url,
        chroma_collection_prefix=f"t{uuid4().hex[:8]}",
        embedding_model="fake-embedding-v1",
    )

    built = build_vector_store(settings)
    assert isinstance(built, ChromaVectorStore)
    try:
        owner, document = uuid4(), uuid4()
        await built.upsert(owner, [record(owner, document, 0, axis(0))])
        info = await built.ensure_collection()
        assert info.name == f"{settings.chroma_collection_prefix}-fake-embedding-v1"
        assert info.metadata["embedding_provider"] == "fake"
        assert info.count == 1
    finally:
        await built.drop_collection()


async def test_a_collection_rebuilt_behind_the_store_is_retryable_then_recreated(
    store: ChromaVectorStore, chroma_url: str
) -> None:
    owner, document = uuid4(), uuid4()
    await store.upsert(owner, [record(owner, document, 0, axis(0))])
    # Another process drops the collection (a rebuild).
    other_handle = ChromaVectorStore.from_url(chroma_url, collection=store.collection)
    await other_handle.drop_collection()

    with pytest.raises(VectorStoreUnavailableError):
        await store.count(owner, document)

    # The next attempt recreates the collection and proceeds.
    assert await store.count(owner, document) == 0
    await store.upsert(owner, [record(owner, document, 0, axis(0))])
    assert await store.count(owner, document) == 1


async def test_calls_are_bounded_by_the_configured_timeout(chroma_url: str) -> None:
    slow = ChromaVectorStore.from_url(
        chroma_url, collection=f"doculens-timeout-{uuid4().hex[:8]}", timeout_seconds=0.000001
    )

    with pytest.raises(VectorStoreUnavailableError):
        await slow.ensure_collection()
