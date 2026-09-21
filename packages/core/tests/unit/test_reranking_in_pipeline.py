"""Reranking inside the retrieval pipeline: top-N retrieval → reranking → final evidence set,
with fakes for the vector store, embeddings, repositories and the reranker."""

import pytest

from doculens.application.reranking import RerankingStage
from doculens.application.retrieval import RetrievalService, build_retriever
from doculens.domain.reranking import RerankerUnavailableError, RerankingLimits, RerankingStatus
from doculens.domain.retrieval import ContextLimits, RetrievalLimits, RetrievalStrategy
from doculens.testing.reranking import FakeReranker
from doculens.testing.retrieval import IndexedCorpus
from doculens.testing.vectors import InMemoryVectorStore

pytestmark = pytest.mark.unit

LIMITS = RetrievalLimits(max_query_characters=200, candidate_limit=10, max_scope_documents=5)
CONTEXT = ContextLimits(max_chunks=6, max_characters=10_000, max_chunks_per_document=6)
TEXTS = [
    "unrelated grocery list with apples",
    "the security review of the login flow found nothing",
    "login flow diagram",
    "quarterly security spending review",
    "another unrelated note",
]


class Corpus(IndexedCorpus[InMemoryVectorStore]):
    def __init__(self) -> None:
        super().__init__(InMemoryVectorStore())

    def service(self, reranking: RerankingStage | None) -> RetrievalService:
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
async def corpus() -> Corpus:
    corpus = Corpus()
    await corpus.index(TEXTS)
    return corpus


async def test_top_n_retrieval_then_reranking_then_final_evidence_set(corpus: Corpus) -> None:
    provider = FakeReranker()
    service = corpus.service(
        RerankingStage(provider, limits=RerankingLimits(top_k=2, timeout_seconds=1))
    )

    result = await service.retrieve(corpus.owner.id, "security review of the login flow")

    # Top-N: every retrieved candidate was offered to the reranker, in retrieval order.
    assert result.stats.hits == 5
    (query, documents, top_n) = provider.calls[0]
    assert query == "security review of the login flow"
    assert len(documents) == 5
    assert top_n == 2
    # Final evidence set: the reranker's top-k, renumbered, with retrieval scores kept.
    assert result.reranking.status is RerankingStatus.APPLIED
    assert result.reranking.considered == 5
    assert result.reranking.kept == 2
    assert [e.text for e in result.evidence] == [TEXTS[1], TEXTS[3]] or [
        e.text for e in result.evidence
    ] == [TEXTS[1], TEXTS[2]]
    assert [e.metadata["rank"] for e in result.evidence] == [1, 2]
    assert all("retrieval_score" in e.metadata for e in result.evidence)
    assert all(e.metadata["retriever"] == "hybrid" for e in result.evidence)
    assert all(e.metadata["reranker"] == "fake" for e in result.evidence)
    # Context is assembled from the final evidence set only.
    assert [item.evidence for item in result.context.items] == list(result.evidence)
    assert result.stats.omitted_from_context == 0


async def test_without_a_reranker_the_pipeline_is_unchanged(corpus: Corpus) -> None:
    plain = await corpus.service(None).retrieve(corpus.owner.id, "login flow")
    disabled = await corpus.service(
        RerankingStage(None, limits=RerankingLimits(top_k=2, timeout_seconds=1))
    ).retrieve(corpus.owner.id, "login flow")

    assert plain.reranking.status is RerankingStatus.DISABLED
    assert disabled.reranking.status is RerankingStatus.DISABLED
    assert plain.evidence == disabled.evidence
    assert len(plain.evidence) == 5


async def test_a_failing_or_slow_reranker_degrades_to_the_retrieval_order(corpus: Corpus) -> None:
    failing = FakeReranker(failures=[RerankerUnavailableError()])
    slow = FakeReranker(delay_seconds=5.0)
    limits = RerankingLimits(top_k=2, timeout_seconds=0.1)
    baseline = await corpus.service(None).retrieve(corpus.owner.id, "login flow")

    for provider in (failing, slow):
        result = await corpus.service(RerankingStage(provider, limits=limits)).retrieve(
            corpus.owner.id, "login flow"
        )
        assert result.reranking.status is RerankingStatus.DEGRADED
        assert result.reranking.error_code == "RERANKER_UNAVAILABLE"
        assert result.evidence == baseline.evidence  # nothing lost, nothing reordered
        assert result.context == baseline.context
        assert result.has_evidence


async def test_reranking_is_skipped_when_retrieval_found_nothing(corpus: Corpus) -> None:
    provider = FakeReranker()
    service = corpus.service(
        RerankingStage(provider, limits=RerankingLimits(top_k=2, timeout_seconds=1))
    )

    result = await service.retrieve(corpus.stranger.id, "login flow")

    assert result.evidence == ()
    assert result.reranking.status is RerankingStatus.SKIPPED
    assert provider.calls == []
