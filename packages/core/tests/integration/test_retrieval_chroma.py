"""The retrieval pipeline against a real ChromaDB server (testcontainers or
``DOCULENS_TEST_CHROMA_URL``).

The first part indexes chunks straight into ChromaDB with an in-memory unit of work and checks
that scope, ownership, READY-only serving, staleness and determinism hold when the filters run
server side. The last test runs the whole stack: PostgreSQL, PyMuPDF, chunking, the fake
embedding provider, ChromaDB, then retrieval through the composition root.
"""

import os
from collections.abc import AsyncIterator, Iterator
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import SecretStr

from doculens.application.chunking import DocumentChunker
from doculens.application.ingestion import (
    DocumentIntakeService,
    DocumentProcessor,
    ProcessingOutcome,
    UploadLimits,
)
from doculens.application.rag import RagQuery
from doculens.application.reranking import RerankingStage
from doculens.application.retrieval import RetrievalService, VectorRetriever, build_retriever
from doculens.domain.chunking import ChunkingConfig, chunk_id
from doculens.domain.documents import ProcessingStatus
from doculens.domain.reranking import RerankerUnavailableError, RerankingLimits, RerankingStatus
from doculens.domain.retrieval import ContextLimits, RetrievalLimits, RetrievalStrategy
from doculens.infrastructure.config import (
    CoreSettings,
    EmbeddingProviderKind,
    RerankerKind,
    VectorStoreKind,
)
from doculens.infrastructure.pdf import ExtractionLimits, PyMuPdfExtractor
from doculens.infrastructure.persistence.database import Database
from doculens.infrastructure.rag import build_rag_service
from doculens.infrastructure.storage import FilesystemObjectStorage
from doculens.infrastructure.vectors import ChromaVectorStore
from doculens.testing.chroma import VectorStoreUnavailableForTestsError, provisioned_chroma_url
from doculens.testing.embeddings import BagOfWordsEmbeddingProvider
from doculens.testing.factories import Factories
from doculens.testing.pdfs import pdf_with_pages
from doculens.testing.reranking import FakeReranker
from doculens.testing.retrieval import IndexedCorpus

pytestmark = pytest.mark.integration

DIMENSIONS = 32
LIMITS = RetrievalLimits(
    max_query_characters=500, candidate_limit=8, min_score=-1.0, max_scope_documents=10
)
CONTEXT = ContextLimits(max_chunks=4, max_characters=5_000, max_chunks_per_document=2)


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
async def vectors(chroma_url: str) -> AsyncIterator[ChromaVectorStore]:
    store = ChromaVectorStore.from_url(
        chroma_url, collection=f"doculens-retrieval-{uuid4().hex[:12]}"
    )
    await store.ensure_collection()
    try:
        yield store
    finally:
        await store.drop_collection()


class Corpus(IndexedCorpus[ChromaVectorStore]):
    """Two users whose chunks share one ChromaDB collection; rows live in an in-memory store."""

    def __init__(self, vectors: ChromaVectorStore) -> None:
        super().__init__(vectors, dimensions=DIMENSIONS)
        self.service = RetrievalService(
            unit_of_work=self.unit_of_work,
            retriever=VectorRetriever(embeddings=self.embeddings, vectors=vectors),
            limits=LIMITS,
            context_limits=CONTEXT,
        )

    def hybrid(self, reranking: RerankingStage | None = None) -> RetrievalService:
        return RetrievalService(
            unit_of_work=self.unit_of_work,
            retriever=build_retriever(
                RetrievalStrategy.HYBRID,
                unit_of_work=self.unit_of_work,
                embeddings=self.embeddings,
                vectors=self.vectors,
                min_score=-1.0,
            ),
            limits=LIMITS,
            context_limits=CONTEXT,
            reranking=reranking,
        )


@pytest.fixture
def corpus(vectors: ChromaVectorStore) -> Corpus:
    return Corpus(vectors)


async def test_evidence_comes_back_structured_from_chroma(corpus: Corpus) -> None:
    document = await corpus.index(
        [
            "Authentication uses short-lived JWT access tokens.",
            "Refresh tokens rotate on every use.",
            "The deployment runs on AWS Lambda.",
        ],
        filename="security.pdf",
    )

    result = await corpus.service.retrieve(
        corpus.owner.id, "How do refresh tokens rotate?", document_ids=[document.id]
    )

    assert result.stats.hits == 3
    best = result.evidence[0]
    assert best.document_id == document.id
    assert best.chunk_id == chunk_id(document.id, 1)
    assert best.page_number == 2
    assert best.text == "Refresh tokens rotate on every use."
    assert -1.0 <= best.score <= 1.0
    assert best.score > result.evidence[-1].score
    assert best.filename == "security.pdf"
    assert best.metadata["chunk_index"] == 1
    assert best.metadata["rank"] == 1
    assert [item.index for item in result.context.items] == [1, 2, 3]
    assert result.context.items[0].evidence == best
    assert result.usage.requests == 1


async def test_chroma_never_returns_another_users_vectors(corpus: Corpus) -> None:
    await corpus.index(["the confidential merger plan"], owner=corpus.stranger.id)
    mine = await corpus.index(["an unrelated grocery list"])

    result = await corpus.service.retrieve(corpus.owner.id, "the confidential merger plan")

    assert result.stats.hits == 1  # filtered server side, not after the fact
    assert [e.document_id for e in result.evidence] == [mine.id]
    assert result.stats.out_of_scope == 0

    theirs = await corpus.service.retrieve(corpus.stranger.id, "the confidential merger plan")
    assert [e.text for e in theirs.evidence] == ["the confidential merger plan"]


async def test_scope_filters_are_applied_inside_chroma(corpus: Corpus) -> None:
    in_collection = await corpus.index(["the topic"], collection=corpus.collection.id)
    selected = await corpus.index(["the topic"])
    await corpus.index(["the topic"])

    by_collection = await corpus.service.retrieve(
        corpus.owner.id, "the topic", collection_id=corpus.collection.id
    )
    by_selection = await corpus.service.retrieve(
        corpus.owner.id, "the topic", document_ids=[selected.id]
    )
    everything = await corpus.service.retrieve(corpus.owner.id, "the topic")

    assert by_collection.stats.hits == 1
    assert [e.document_id for e in by_collection.evidence] == [in_collection.id]
    assert by_selection.stats.hits == 1
    assert [e.document_id for e in by_selection.evidence] == [selected.id]
    assert everything.stats.hits == 3
    assert len(everything.evidence) == 1  # identical text: duplicates removed
    assert everything.stats.duplicates == 2


async def test_vectors_of_documents_that_are_not_ready_stay_unserved(corpus: Corpus) -> None:
    await corpus.index(["being re-indexed right now"], status=ProcessingStatus.INDEXING)
    ready = await corpus.index(["a finished document"])
    assert await corpus.vectors.count(corpus.owner.id) == 2

    result = await corpus.service.retrieve(corpus.owner.id, "being re-indexed right now")

    assert result.stats.documents_excluded == 1
    assert result.stats.hits == 1
    assert [e.document_id for e in result.evidence] == [ready.id]


async def test_a_re_indexed_chunk_is_not_served_with_its_old_vector(corpus: Corpus) -> None:
    document = await corpus.index(["original wording", "untouched"])
    changed = chunk_id(document.id, 0)
    corpus.store.chunks[changed] = replace(
        corpus.store.chunks[changed],
        text="rewritten wording",
        metadata={"page_number": 1, "content_hash": "h:rewritten wording"},
    )

    result = await corpus.service.retrieve(corpus.owner.id, "original wording")

    assert result.stats.hits == 2
    assert result.stats.stale == 1
    assert [e.text for e in result.evidence] == ["untouched"]


async def test_candidate_limit_and_determinism_hold_against_chroma(corpus: Corpus) -> None:
    await corpus.index([f"passage number {i} about the budget" for i in range(12)])

    first = await corpus.service.retrieve(corpus.owner.id, "the budget passage")
    second = await corpus.service.retrieve(corpus.owner.id, "the budget passage")

    assert first.stats.hits == LIMITS.candidate_limit
    assert len(first.evidence) == LIMITS.candidate_limit
    assert len(first.context.items) == CONTEXT.max_chunks
    assert first.evidence == second.evidence
    assert first.context == second.context


async def test_hybrid_retrieval_fuses_chroma_neighbours_with_keyword_matches(
    corpus: Corpus,
) -> None:
    document = await corpus.index(
        [
            "the security review covered the login flow",
            "login flow diagram, serial ZX-77-Q",
            "a review of quarterly security spending",
            "unrelated grocery list",
        ]
    )
    # Chunk 3 was re-chunked after indexing: its row changed, its vector did not.
    corpus.store.chunks[chunk_id(document.id, 3)] = replace(
        corpus.store.chunks[chunk_id(document.id, 3)],
        text="serial ZX-77-Q appears here too",
        metadata={"page_number": 4, "content_hash": "h:rewritten"},
    )

    fused = await corpus.hybrid().retrieve(corpus.owner.id, "security review of the login flow")
    assert fused.retriever == "hybrid"
    assert fused.evidence[0].chunk_index == 0  # found by both sides
    assert {e.chunk_index for e in fused.evidence} >= {0, 1, 2}
    assert fused.usage.requests == 1

    # A serial number no vector is close to still ranks first through the keyword side, while
    # the stale chunk (matched by keyword, returned by ChromaDB) is dropped by both paths.
    keyword_only = await corpus.hybrid().retrieve(corpus.owner.id, "ZX-77-Q")
    assert keyword_only.evidence[0].chunk_index == 1
    assert 3 not in {e.chunk_index for e in keyword_only.evidence}
    assert keyword_only.stats.stale == 1


async def test_reranking_narrows_chroma_candidates_to_the_final_evidence_set(
    corpus: Corpus,
) -> None:
    await corpus.index(
        [
            "unrelated grocery list with apples",
            "the security review of the login flow found nothing",
            "login flow diagram",
            "quarterly security spending review",
            "another unrelated note",
        ]
    )
    provider = FakeReranker()
    limits = RerankingLimits(top_k=2, timeout_seconds=2.0)

    result = await corpus.hybrid(RerankingStage(provider, limits=limits)).retrieve(
        corpus.owner.id, "security review of the login flow"
    )

    assert result.stats.hits == 5  # top-N from ChromaDB plus keyword matches
    assert len(provider.calls[0][1]) == 5
    assert result.reranking.status is RerankingStatus.APPLIED
    assert result.reranking.kept == 2
    assert result.evidence[0].text == "the security review of the login flow found nothing"
    assert len(result.evidence) == 2
    assert all("retrieval_score" in e.metadata for e in result.evidence)
    assert [item.evidence for item in result.context.items] == list(result.evidence)

    degraded = await corpus.hybrid(
        RerankingStage(FakeReranker(failures=[RerankerUnavailableError()]), limits=limits)
    ).retrieve(corpus.owner.id, "security review of the login flow")
    assert degraded.reranking.status is RerankingStatus.DEGRADED
    assert len(degraded.evidence) == 5
    assert degraded.evidence[0].text == "the security review of the login flow found nothing"

    slow = await corpus.hybrid(
        RerankingStage(
            FakeReranker(delay_seconds=30), limits=RerankingLimits(top_k=2, timeout_seconds=0.2)
        )
    ).retrieve(corpus.owner.id, "security review of the login flow")
    assert slow.reranking.status is RerankingStatus.DEGRADED
    assert slow.reranking.latency_ms < 5_000


# -- full stack -----------------------------------------------------------------------------------

UPLOAD_LIMITS = UploadLimits(
    max_file_size_bytes=256 * 1024, max_pages_per_document=10, max_documents_per_user=20
)


async def test_a_processed_pdf_is_retrievable_through_the_rag_boundary(
    database: Database, settings: CoreSettings, tmp_path: Path, vectors: ChromaVectorStore
) -> None:
    embeddings = BagOfWordsEmbeddingProvider(dimensions=DIMENSIONS)
    storage = FilesystemObjectStorage(tmp_path / "objects")
    owner = Factories.user("alice@example.com")
    stranger = Factories.user("mallory@example.com")
    async with database.unit_of_work() as uow:
        await uow.users.add(owner)
        await uow.users.add(stranger)
        await uow.commit()
    intake = DocumentIntakeService(
        unit_of_work=database.unit_of_work, storage=storage, limits=UPLOAD_LIMITS
    )
    processor = DocumentProcessor(
        unit_of_work=database.unit_of_work,
        storage=storage,
        extractor=PyMuPdfExtractor(
            ExtractionLimits(
                timeout_seconds=60.0,
                memory_limit_bytes=None,
                max_characters_per_page=100_000,
                max_total_characters=1_000_000,
            )
        ),
        chunker=DocumentChunker(ChunkingConfig(chunk_size=24, chunk_overlap=4, min_chunk_size=4)),
        embeddings=embeddings,
        vectors=vectors,
        limits=UPLOAD_LIMITS,
    )
    data = pdf_with_pages(
        [
            "Revenue grew twelve percent in the third quarter. Costs were flat.",
            "The security review found no critical vulnerabilities in the login flow.",
        ]
    )
    document = await intake.accept(
        owner.id, filename="annual.pdf", declared_mime_type="application/pdf", data=data
    )
    report = await processor.process(document.id)
    assert report.outcome is ProcessingOutcome.PROCESSED
    rag = build_rag_service(
        retrieval_settings(settings),
        unit_of_work=database.unit_of_work,
        embeddings=embeddings,
        vectors=vectors,
    )

    result = await rag.retrieve(
        RagQuery(
            owner_id=owner.id, question="Were critical vulnerabilities found in the login flow?"
        )
    )

    async with database.unit_of_work() as uow:
        chunks = {c.id: c for c in await uow.document_content.list_chunks(owner.id, document.id)}
    assert result.has_evidence
    best = result.evidence[0]
    assert best.document_id == document.id
    assert best.page_number == 2
    assert "login flow" in best.text
    assert best.text == chunks[best.chunk_id].text
    assert best.filename == "annual.pdf"
    assert all(e.chunk_id in chunks for e in result.evidence)
    assert result.context.items[0].evidence == best
    assert result.retriever == "hybrid"
    assert result.reranking.status is RerankingStatus.DISABLED

    reranked = await build_rag_service(
        retrieval_settings(settings, reranker=RerankerKind.FAKE),
        unit_of_work=database.unit_of_work,
        embeddings=embeddings,
        vectors=vectors,
    ).retrieve(RagQuery(owner_id=owner.id, question="critical vulnerabilities in the login flow"))
    assert reranked.reranking.status is RerankingStatus.APPLIED
    assert reranked.reranking.provider == "fake"
    assert 0 < len(reranked.evidence) <= 2
    assert reranked.evidence[0].chunk_id == best.chunk_id
    assert reranked.evidence[0].metadata["retrieval_score"] == best.score

    nothing = await rag.retrieve(RagQuery(owner_id=stranger.id, question="login flow"))
    assert nothing.evidence == ()
    assert nothing.stats.documents_in_scope == 0


def retrieval_settings(
    settings: CoreSettings, *, reranker: RerankerKind = RerankerKind.NONE
) -> CoreSettings:
    """Settings for the composition root; the injected fakes make the provider values moot."""
    return CoreSettings(
        _env_file=None,
        database_url=SecretStr(settings.database_url.get_secret_value()),
        embedding_provider=EmbeddingProviderKind.FAKE,
        vector_store=VectorStoreKind.MEMORY,
        retrieval_candidates=8,
        context_max_chunks=4,
        reranker_provider=reranker,
        rerank_top_k=2,
    )
