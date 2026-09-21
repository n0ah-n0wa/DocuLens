"""Hybrid retrieval with fakes: semantic, keyword and combined strategies, filtering, no results
and cross-document questions (§18, §27). The keyword side runs through the in-memory repository;
the semantic side through the in-memory vector store."""

import asyncio

import pytest

from doculens.application.retrieval import (
    HybridRetriever,
    KeywordRetriever,
    RetrievalService,
    Retrieved,
    VectorRetriever,
    build_retriever,
)
from doculens.domain.documents import ProcessingStatus
from doculens.domain.embeddings import EmbeddingProviderUnavailableError
from doculens.domain.retrieval import (
    ContextLimits,
    PreparedQuery,
    RetrievalLimits,
    RetrievalStrategy,
)
from doculens.domain.vectors import SearchFilter
from doculens.testing.retrieval import IndexedCorpus
from doculens.testing.vectors import InMemoryVectorStore

pytestmark = pytest.mark.unit

LIMITS = RetrievalLimits(max_query_characters=200, candidate_limit=10, max_scope_documents=5)
CONTEXT = ContextLimits(max_chunks=6, max_characters=10_000, max_chunks_per_document=3)


class Corpus(IndexedCorpus[InMemoryVectorStore]):
    def __init__(self) -> None:
        super().__init__(InMemoryVectorStore())

    def service(self, strategy: RetrievalStrategy, *, min_score: float = -1.0) -> RetrievalService:
        retriever = build_retriever(
            strategy,
            unit_of_work=self.unit_of_work,
            embeddings=self.embeddings,
            vectors=self.vectors,
            min_score=min_score,
        )
        return RetrievalService(
            unit_of_work=self.unit_of_work,
            retriever=retriever,
            limits=LIMITS,
            context_limits=CONTEXT,
        )


@pytest.fixture
def corpus() -> Corpus:
    return Corpus()


async def test_semantic_retrieval_ranks_by_vector_similarity_only(corpus: Corpus) -> None:
    document = await corpus.index(
        ["refresh tokens rotate on use", "access tokens expire quickly", "grocery list"]
    )
    service = corpus.service(RetrievalStrategy.SEMANTIC)

    result = await service.retrieve(corpus.owner.id, "refresh tokens rotate on use")

    assert result.retriever == "vector"
    assert result.evidence[0].chunk_index == 0
    assert result.evidence[0].score == pytest.approx(1.0)
    assert result.evidence[0].metadata["retriever"] == "vector"
    assert result.evidence[0].document_id == document.id
    assert corpus.embeddings.query_calls == ["refresh tokens rotate on use"]
    assert result.usage.requests == 1


async def test_semantic_threshold_drops_weak_neighbours(corpus: Corpus) -> None:
    await corpus.index(["refresh tokens rotate on use", "unrelated grocery list"])

    result = await corpus.service(RetrievalStrategy.SEMANTIC, min_score=0.9).retrieve(
        corpus.owner.id, "refresh tokens rotate on use"
    )

    assert [e.chunk_index for e in result.evidence] == [0]
    assert result.stats.below_threshold == 1


async def test_keyword_retrieval_matches_terms_without_embedding_anything(corpus: Corpus) -> None:
    document = await corpus.index(
        ["The invoice number is INV-2291.", "Payment terms are thirty days.", "Nothing here."]
    )
    service = corpus.service(RetrievalStrategy.KEYWORD)

    result = await service.retrieve(corpus.owner.id, "invoice 2291")

    assert result.retriever == "keyword"
    assert [e.chunk_index for e in result.evidence] == [0]
    assert result.evidence[0].document_id == document.id
    assert result.evidence[0].text == "The invoice number is INV-2291."
    assert result.evidence[0].page_number == 1
    assert result.evidence[0].metadata["retriever"] == "keyword"
    assert result.evidence[0].metadata["embedding_model"] == ""
    assert corpus.embeddings.query_calls == []
    assert result.usage.requests == 0


async def test_keyword_retrieval_ranks_chunks_matching_more_terms_first(corpus: Corpus) -> None:
    await corpus.index(["alpha only", "alpha and beta", "alpha, beta and gamma together", "delta"])

    result = await corpus.service(RetrievalStrategy.KEYWORD).retrieve(
        corpus.owner.id, "Alpha BETA gamma"
    )

    assert [e.chunk_index for e in result.evidence] == [2, 1, 0]
    assert [e.score for e in result.evidence] == [3.0, 2.0, 1.0]  # distinct terms matched


async def test_combined_retrieval_fuses_both_rankings(corpus: Corpus) -> None:
    await corpus.index(
        [
            "the security review covered the login flow",  # both retrievers
            "login flow diagram",  # keyword strong, some vector overlap
            "a review of quarterly security spending",  # semantic-ish overlap only
            "unrelated grocery list",
        ]
    )
    service = corpus.service(RetrievalStrategy.HYBRID)

    result = await service.retrieve(corpus.owner.id, "security review of the login flow")

    assert result.retriever == "hybrid"
    assert result.evidence[0].chunk_index == 0  # found by both: outranks everything
    assert result.evidence[0].metadata["retriever"] == "hybrid"
    found = {e.chunk_index for e in result.evidence}
    assert {0, 1, 2} <= found
    assert corpus.embeddings.query_calls == ["security review of the login flow"]
    assert result.usage.requests == 1
    # Fused scores are scaled reciprocal ranks: 1.0 means first on both sides.
    assert result.evidence[0].score == pytest.approx(1.0)
    assert all(0 < e.score <= 1.0 for e in result.evidence)


async def test_combined_retrieval_finds_what_only_one_side_finds(corpus: Corpus) -> None:
    await corpus.index(["serial number ZX-77-Q", "a sentence about cats and dogs"])
    semantic = VectorRetriever(embeddings=corpus.embeddings, vectors=corpus.vectors, min_score=0.99)
    hybrid = HybridRetriever(
        semantic=semantic, keyword=KeywordRetriever(unit_of_work=corpus.unit_of_work)
    )
    service = RetrievalService(
        unit_of_work=corpus.unit_of_work, retriever=hybrid, limits=LIMITS, context_limits=CONTEXT
    )

    # The strict semantic threshold drops everything; the keyword side still finds the serial.
    result = await service.retrieve(corpus.owner.id, "ZX-77-Q")

    assert [e.chunk_index for e in result.evidence] == [0]
    assert result.stats.below_threshold >= 1


@pytest.mark.parametrize("strategy", list(RetrievalStrategy))
async def test_filtering_applies_to_every_strategy(
    corpus: Corpus, strategy: RetrievalStrategy
) -> None:
    in_collection = await corpus.index(["the shared phrase"], collection=corpus.collection.id)
    selected = await corpus.index(["the shared phrase"])
    await corpus.index(["the shared phrase"])
    await corpus.index(["the shared phrase"], status=ProcessingStatus.EMBEDDING)
    await corpus.index(["the shared phrase"], owner=corpus.stranger.id)
    service = corpus.service(strategy)

    by_collection = await service.retrieve(
        corpus.owner.id, "the shared phrase", collection_id=corpus.collection.id
    )
    by_selection = await service.retrieve(
        corpus.owner.id, "the shared phrase", document_ids=[selected.id]
    )
    everything = await service.retrieve(corpus.owner.id, "the shared phrase")

    assert [e.document_id for e in by_collection.evidence] == [in_collection.id]
    assert by_collection.stats.hits == 1
    assert [e.document_id for e in by_selection.evidence] == [selected.id]
    assert by_selection.stats.hits == 1
    assert everything.stats.hits == 3  # the stranger's and the non-READY chunks never surface
    assert everything.stats.documents_excluded == 1
    assert len(everything.evidence) == 1  # identical text is one piece of evidence
    assert everything.stats.duplicates == 2
    assert all(e.document_id != corpus.stranger.id for e in everything.evidence)


@pytest.mark.parametrize("strategy", list(RetrievalStrategy))
async def test_no_results_yields_an_empty_structured_result(
    corpus: Corpus, strategy: RetrievalStrategy
) -> None:
    service = corpus.service(strategy)

    without_documents = await service.retrieve(corpus.owner.id, "anything at all")
    assert without_documents.evidence == ()
    assert without_documents.context.is_empty
    assert without_documents.stats.hits == 0
    assert without_documents.stats.documents_in_scope == 0
    assert corpus.embeddings.query_calls == []

    await corpus.index(["completely different words"])
    strict = corpus.service(strategy, min_score=0.99)
    nothing = await strict.retrieve(corpus.owner.id, "zzz")
    assert nothing.evidence == ()
    assert nothing.context.is_empty
    assert not nothing.has_evidence


async def test_a_question_of_only_punctuation_skips_the_keyword_side(corpus: Corpus) -> None:
    await corpus.index(["some text"])

    result = await corpus.service(RetrievalStrategy.KEYWORD).retrieve(corpus.owner.id, "?!?")

    assert result.evidence == ()
    assert result.stats.hits == 0


@pytest.mark.parametrize("strategy", list(RetrievalStrategy))
async def test_cross_document_questions_draw_evidence_from_each_document(
    corpus: Corpus, strategy: RetrievalStrategy
) -> None:
    architecture = await corpus.index(
        ["authentication uses signed session cookies", "service runs on kubernetes"],
        filename="Architecture.pdf",
    )
    security = await corpus.index(
        ["authentication uses rotating refresh tokens", "pentest found no critical issues"],
        filename="Security.pdf",
    )
    await corpus.index(["a cookbook chapter on bread"], filename="Cookbook.pdf")
    service = corpus.service(strategy)

    result = await service.retrieve(
        corpus.owner.id,
        "Compare authentication mechanisms",
        document_ids=[architecture.id, security.id],
    )

    top_two = {e.document_id for e in result.evidence[:2]}
    assert top_two == {architecture.id, security.id}
    assert {e.text for e in result.evidence[:2]} == {
        "authentication uses signed session cookies",
        "authentication uses rotating refresh tokens",
    }
    assert {e.filename for e in result.evidence[:2]} == {"Architecture.pdf", "Security.pdf"}
    assert all(e.document_id in {architecture.id, security.id} for e in result.evidence)
    assert set(result.context.document_ids) == {architecture.id, security.id}


async def test_the_strategy_factory_returns_the_configured_retriever(corpus: Corpus) -> None:
    def build(strategy: RetrievalStrategy) -> str:
        return build_retriever(
            strategy,
            unit_of_work=corpus.unit_of_work,
            embeddings=corpus.embeddings,
            vectors=corpus.vectors,
            min_score=0.0,
        ).name

    assert build(RetrievalStrategy.SEMANTIC) == "vector"
    assert build(RetrievalStrategy.KEYWORD) == "keyword"
    assert build(RetrievalStrategy.HYBRID) == "hybrid"
    assert RetrievalStrategy("hybrid") is RetrievalStrategy.HYBRID
    with pytest.raises(ValueError, match="not a valid"):
        RetrievalStrategy("bm25")


async def test_a_failing_side_cancels_the_other_and_reports_the_failure_itself(
    corpus: Corpus,
) -> None:
    await corpus.index(["some text"])

    class Failing:
        name = "failing"

        async def retrieve(
            self, query: PreparedQuery, *, scope: SearchFilter, limit: int
        ) -> Retrieved:
            del query, scope, limit
            raise EmbeddingProviderUnavailableError(provider="fake")

    class Slow:
        name = "slow"
        cancelled = False

        async def retrieve(
            self, query: PreparedQuery, *, scope: SearchFilter, limit: int
        ) -> Retrieved:
            del query, scope, limit
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                Slow.cancelled = True
                raise
            return Retrieved(hits=[])

    service = RetrievalService(
        unit_of_work=corpus.unit_of_work,
        retriever=HybridRetriever(semantic=Failing(), keyword=Slow()),
        limits=LIMITS,
        context_limits=CONTEXT,
    )

    started = asyncio.get_running_loop().time()
    with pytest.raises(EmbeddingProviderUnavailableError):
        await service.retrieve(corpus.owner.id, "some text")

    assert asyncio.get_running_loop().time() - started < 5
    assert Slow.cancelled
