"""The retrieval pipeline and the RAG boundary against fakes: in-memory vector store, fake
embedding provider and in-memory unit of work."""

import asyncio
from collections.abc import Sequence
from dataclasses import replace
from typing import ClassVar
from uuid import UUID, uuid4

import pytest

from doculens.application.answering import AnswerService
from doculens.application.rag import RagQuery, RagService
from doculens.application.retrieval import RetrievalService, Retrieved, VectorRetriever
from doculens.domain.chunking import chunk_id
from doculens.domain.collections import CollectionNotFoundError
from doculens.domain.documents import DocumentNotFoundError, ProcessingStatus
from doculens.domain.embeddings import EmbeddingProviderUnavailableError
from doculens.domain.prompting import PromptBuilder
from doculens.domain.retrieval import (
    ContextLimits,
    InvalidQueryError,
    InvalidRetrievalScopeError,
    PreparedQuery,
    RetrievalLimits,
    RetrievalTimeoutError,
)
from doculens.domain.vectors import SearchFilter, SearchHit, VectorStoreUnavailableError
from doculens.testing.factories import Factories
from doculens.testing.fakes import InMemoryDocumentRepository
from doculens.testing.llm import FakeLLMProvider
from doculens.testing.retrieval import IndexedCorpus
from doculens.testing.vectors import InMemoryVectorStore

pytestmark = pytest.mark.unit

DIMENSIONS = 32
LIMITS = RetrievalLimits(
    max_query_characters=200, candidate_limit=10, min_score=-1.0, max_scope_documents=3
)
CONTEXT = ContextLimits(max_chunks=4, max_characters=10_000, max_chunks_per_document=2)


class World(IndexedCorpus[InMemoryVectorStore]):
    """An owner with indexed documents, plus a stranger whose vectors share the index."""

    def __init__(self) -> None:
        super().__init__(InMemoryVectorStore(), dimensions=DIMENSIONS)
        self.service = RetrievalService(
            unit_of_work=self.unit_of_work,
            retriever=VectorRetriever(embeddings=self.embeddings, vectors=self.vectors),
            limits=LIMITS,
            context_limits=CONTEXT,
        )
        self.rag = RagService(
            retrieval=self.service,
            answering=AnswerService(
                unit_of_work=self.unit_of_work,
                retrieval=self.service,
                prompt_builder=PromptBuilder(),
                llm=FakeLLMProvider(),
            ),
        )


@pytest.fixture
def world() -> World:
    return World()


async def test_evidence_is_structured_and_comes_from_the_owners_ready_documents(
    world: World,
) -> None:
    document = await world.index(
        ["alpha beta", "the exact question text", "gamma delta"], filename="answers.pdf"
    )

    result = await world.service.retrieve(world.owner.id, "  the exact   question text ")

    assert result.query.text == "the exact question text"
    assert result.query.original == "  the exact   question text "
    assert result.has_evidence
    best = result.evidence[0]
    assert best.document_id == document.id
    assert best.chunk_id == chunk_id(document.id, 1)
    assert best.page_number == 2
    assert best.text == "the exact question text"
    assert best.score == pytest.approx(1.0)
    assert best.filename == "answers.pdf"
    assert best.metadata["chunk_index"] == 1
    assert best.metadata["rank"] == 1
    assert best.metadata["retriever"] == "vector"
    assert best.metadata["embedding_model"] == "fake-bag-of-words-v1"
    assert result.retriever == "vector"
    assert best.metadata["collection_id"] is None
    assert result.context.items[0].index == 1
    assert result.context.items[0].evidence == best
    assert world.embeddings.query_calls == ["the exact question text"]
    assert result.usage.requests == 1
    assert result.stats.documents_in_scope == 1
    assert result.stats.hits == 3
    assert result.stats.latency_ms >= 0


async def test_another_users_vectors_are_never_evidence_even_with_identical_text(
    world: World,
) -> None:
    await world.index(["shared secret sentence"], owner=world.stranger.id)
    mine = await world.index(["something unrelated"])

    result = await world.service.retrieve(world.owner.id, "shared secret sentence")

    assert [e.document_id for e in result.evidence] == [mine.id]
    assert all(e.text != "shared secret sentence" for e in result.evidence)
    assert result.stats.out_of_scope == 0  # the store never returned it in the first place

    nothing = await world.service.retrieve(world.stranger.id, "something unrelated")
    assert [e.text for e in nothing.evidence] == ["shared secret sentence"]


async def test_documents_that_are_not_ready_are_not_served(world: World) -> None:
    await world.index(["being re-indexed"], status=ProcessingStatus.EMBEDDING)
    await world.index(["failed"], status=ProcessingStatus.FAILED)
    await world.index(["deleting"], status=ProcessingStatus.DELETING)

    result = await world.service.retrieve(world.owner.id, "being re-indexed")

    assert result.evidence == ()
    assert result.context.is_empty
    assert result.stats.documents_in_scope == 0
    assert result.stats.documents_excluded == 3
    # No READY document: the provider is never called and nothing is spent.
    assert world.embeddings.query_calls == []
    assert result.usage.requests == 0


async def test_selected_documents_restrict_retrieval_to_exactly_those(world: World) -> None:
    wanted = await world.index(["the answer"])
    unwanted = await world.index(["the answer"])

    result = await world.service.retrieve(world.owner.id, "the answer", document_ids=[wanted.id])

    assert [e.document_id for e in result.evidence] == [wanted.id]
    assert result.scope.document_ids == (wanted.id,)
    assert unwanted.id not in result.context.document_ids


async def test_a_collection_scope_covers_its_documents_only(world: World) -> None:
    inside = await world.index(["the answer"], collection=world.collection.id)
    await world.index(["the answer"])

    result = await world.service.retrieve(
        world.owner.id, "the answer", collection_id=world.collection.id
    )

    assert [e.document_id for e in result.evidence] == [inside.id]
    assert result.evidence[0].metadata["collection_id"] == str(world.collection.id)


async def test_a_foreign_or_unknown_selection_is_not_found_never_forbidden(world: World) -> None:
    foreign = await world.index(["theirs"], owner=world.stranger.id)
    mine = await world.index(["mine"])
    foreign_collection = Factories.collection(world.stranger.id, "Theirs")
    world.store.collections[foreign_collection.id] = foreign_collection

    with pytest.raises(DocumentNotFoundError):
        await world.service.retrieve(world.owner.id, "q", document_ids=[mine.id, foreign.id])
    with pytest.raises(DocumentNotFoundError):
        await world.service.retrieve(world.owner.id, "q", document_ids=[uuid4()])
    with pytest.raises(CollectionNotFoundError):
        await world.service.retrieve(world.owner.id, "q", collection_id=foreign_collection.id)
    with pytest.raises(CollectionNotFoundError):
        await world.service.retrieve(world.owner.id, "q", collection_id=uuid4())
    assert world.embeddings.query_calls == []


async def test_invalid_questions_and_scopes_are_refused_before_any_work(world: World) -> None:
    await world.index(["x"])

    with pytest.raises(InvalidQueryError):
        await world.service.retrieve(world.owner.id, "   ")
    with pytest.raises(InvalidQueryError):
        await world.service.retrieve(world.owner.id, "q" * 201)
    with pytest.raises(InvalidRetrievalScopeError):
        await world.service.retrieve(world.owner.id, "q", document_ids=[])
    with pytest.raises(InvalidRetrievalScopeError, match="at most 3"):
        await world.service.retrieve(world.owner.id, "q", document_ids=[uuid4() for _ in range(4)])
    with pytest.raises(InvalidRetrievalScopeError):
        await world.service.retrieve(
            world.owner.id, "q", document_ids=[uuid4()], collection_id=uuid4()
        )
    assert world.embeddings.query_calls == []


async def test_stale_vectors_are_dropped_because_the_chunk_rows_are_the_truth(
    world: World,
) -> None:
    document = await world.index(["old text", "kept text"])
    changed = chunk_id(document.id, 0)
    world.store.chunks[changed] = replace(
        world.store.chunks[changed],
        text="new text",
        metadata={"page_number": 1, "content_hash": "h:new text"},
    )
    removed = chunk_id(document.id, 1)
    del world.store.chunks[removed]

    result = await world.service.retrieve(world.owner.id, "old text")

    assert result.evidence == ()
    assert result.stats.hits == 2
    assert result.stats.stale == 2


async def test_evidence_text_is_read_from_the_database_not_the_vector_store(world: World) -> None:
    document = await world.index(["database text"])
    vector = world.vectors.records[str(chunk_id(document.id, 0))]
    world.vectors.records[vector.id] = replace(vector, text="tampered vector copy")

    result = await world.service.retrieve(world.owner.id, "database text")

    assert result.evidence[0].text == "database text"


async def test_the_score_threshold_and_context_limits_apply(world: World) -> None:
    await world.index([f"passage {i}" for i in range(6)])
    strict = RetrievalService(
        unit_of_work=world.unit_of_work,
        retriever=VectorRetriever(
            embeddings=world.embeddings, vectors=world.vectors, min_score=0.99
        ),
        limits=replace(LIMITS, candidate_limit=6),
        context_limits=ContextLimits(max_chunks=2, max_characters=10_000),
    )

    result = await strict.retrieve(world.owner.id, "passage 3")

    assert [e.text for e in result.evidence] == ["passage 3"]
    assert result.stats.below_threshold == 5
    assert result.stats.omitted_from_context == 0

    relaxed = await world.service.retrieve(world.owner.id, "passage 3")
    assert len(relaxed.evidence) == 6
    assert len(relaxed.context.items) == CONTEXT.max_chunks
    assert relaxed.stats.omitted_from_context == 2
    assert relaxed.context.items[0].evidence.text == "passage 3"


async def test_source_diversity_across_selected_documents(world: World) -> None:
    first = await world.index(["the topic a", "the topic b", "the topic c"])
    second = await world.index(["something else entirely"])
    world.embeddings.query_calls.clear()

    result = await world.service.retrieve(
        world.owner.id, "the topic", document_ids=[first.id, second.id]
    )

    assert result.context.document_ids == (first.id, second.id)
    per_document = [item.evidence.document_id for item in result.context.items]
    assert per_document.count(first.id) == 3  # 2 by the cap, then the free slot
    assert per_document.count(second.id) == 1


async def test_retrieval_is_deterministic(world: World) -> None:
    await world.index(["one", "two", "three"])

    first = await world.service.retrieve(world.owner.id, "two")
    second = await world.service.retrieve(world.owner.id, "two")

    assert first.evidence == second.evidence
    assert first.context == second.context


async def test_provider_and_store_outages_propagate_as_retryable_errors(world: World) -> None:
    await world.index(["x"])
    world.embeddings.failures.append(EmbeddingProviderUnavailableError(provider="fake"))

    with pytest.raises(EmbeddingProviderUnavailableError):
        await world.service.retrieve(world.owner.id, "x")

    class BrokenVectors(InMemoryVectorStore):
        async def search(
            self, query: Sequence[float], *, scope: SearchFilter, limit: int
        ) -> list[SearchHit]:
            del query, scope, limit
            raise VectorStoreUnavailableError

    broken = RetrievalService(
        unit_of_work=world.unit_of_work,
        retriever=VectorRetriever(embeddings=world.embeddings, vectors=BrokenVectors()),
        limits=LIMITS,
        context_limits=CONTEXT,
    )
    with pytest.raises(VectorStoreUnavailableError):
        await broken.retrieve(world.owner.id, "x")


async def test_the_query_embedding_is_the_query_embedding_not_a_document_one(world: World) -> None:
    await world.index(["x"])

    await world.service.retrieve(world.owner.id, "x")

    assert world.embeddings.document_calls == [["x"]]  # from indexing only
    assert world.embeddings.query_calls == ["x"]


async def test_a_custom_retriever_plugs_in_behind_the_port(world: World) -> None:
    document = await world.index(["from the custom retriever"])

    class RecordingRetriever:
        name = "custom"
        calls: ClassVar[list[tuple[str, int]]] = []

        async def retrieve(
            self, query: PreparedQuery, *, scope: SearchFilter, limit: int
        ) -> Retrieved:
            self.calls.append((query.text, limit))
            embedded = await world.embeddings.embed_query(query.text)
            return Retrieved(
                hits=await world.vectors.search(embedded.single, scope=scope, limit=limit)
            )

    service = RetrievalService(
        unit_of_work=world.unit_of_work,
        retriever=RecordingRetriever(),
        limits=LIMITS,
        context_limits=CONTEXT,
    )

    result = await service.retrieve(world.owner.id, "from the custom retriever")

    assert RecordingRetriever.calls == [("from the custom retriever", LIMITS.candidate_limit)]
    assert [e.document_id for e in result.evidence] == [document.id]


async def test_the_rag_boundary_validates_the_scope_and_delegates(world: World) -> None:
    document = await world.index(["answer"])

    result = await world.rag.retrieve(
        RagQuery(owner_id=world.owner.id, question="answer", document_ids=[document.id])
    )
    assert [e.chunk_id for e in result.evidence] == [chunk_id(document.id, 0)]

    with pytest.raises(InvalidRetrievalScopeError):
        await world.rag.retrieve(
            RagQuery(owner_id=world.owner.id, question="answer", document_ids=[])
        )


async def test_retrieval_is_bounded_by_the_per_question_timeout(world: World) -> None:
    await world.index(["x"])

    class StalledRetriever:
        name = "stalled"
        cancelled = False

        async def retrieve(
            self, query: PreparedQuery, *, scope: SearchFilter, limit: int
        ) -> Retrieved:
            del query, scope, limit
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                StalledRetriever.cancelled = True
                raise
            return Retrieved(hits=[])

    service = RetrievalService(
        unit_of_work=world.unit_of_work,
        retriever=StalledRetriever(),
        limits=replace(LIMITS, timeout_seconds=0.05),
        context_limits=CONTEXT,
    )

    started = asyncio.get_running_loop().time()
    with pytest.raises(RetrievalTimeoutError):
        await service.retrieve(world.owner.id, "x")

    assert asyncio.get_running_loop().time() - started < 5
    assert StalledRetriever.cancelled


async def test_selected_documents_are_resolved_with_one_owner_query(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    documents = [await world.index([f"text {i}"]) for i in range(3)]

    async def no_single_reads(self: object, owner_id: UUID, document_id: UUID) -> None:
        del self, owner_id, document_id
        message = "per-document reads must not be used to resolve a selection"
        raise AssertionError(message)

    monkeypatch.setattr(InMemoryDocumentRepository, "get", no_single_reads)

    result = await world.service.retrieve(
        world.owner.id, "text 1", document_ids=[d.id for d in documents]
    )

    assert result.stats.documents_in_scope == 3
    with pytest.raises(DocumentNotFoundError):
        await world.service.retrieve(world.owner.id, "text 1", document_ids=[uuid4()])


async def test_a_collection_scope_is_resolved_by_the_database_not_the_vector_metadata(
    world: World,
) -> None:
    moved = await world.index(["the answer"], collection=world.collection.id)
    # The vector store's collection metadata lags behind the move (e.g. the update failed).
    vector = world.vectors.records[str(chunk_id(moved.id, 0))]
    world.vectors.records[vector.id] = replace(
        vector, metadata=replace(vector.metadata, collection_id=None)
    )

    result = await world.service.retrieve(
        world.owner.id, "the answer", collection_id=world.collection.id
    )

    assert [e.document_id for e in result.evidence] == [moved.id]
