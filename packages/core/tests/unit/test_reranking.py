"""The reranking stage on its own (§19): top-N in, top-k out, bounded latency, fail-open."""

import asyncio
from uuid import UUID, uuid4

import pytest

from doculens.application.reranking import RerankingStage
from doculens.domain.chunking import chunk_id
from doculens.domain.errors import DependencyUnavailableError
from doculens.domain.reranking import (
    RerankerResponseInvalidError,
    RerankerUnavailableError,
    RerankingLimits,
    RerankingStatus,
    RerankScore,
    apply_reranking,
    validate_scores,
)
from doculens.domain.retrieval import Evidence, PreparedQuery
from doculens.testing.reranking import FakeReranker, word_overlap

pytestmark = pytest.mark.unit

QUERY = PreparedQuery(original="login flow security review", text="login flow security review")
LIMITS = RerankingLimits(top_k=2, timeout_seconds=0.2)


def _evidence(document: UUID, index: int, text: str, score: float) -> Evidence:
    return Evidence(
        document_id=document,
        chunk_id=chunk_id(document, index),
        page_number=index + 1,
        text=text,
        score=score,
        metadata={"chunk_index": index, "rank": index + 1, "retriever": "hybrid"},
    )


@pytest.fixture
def candidates() -> list[Evidence]:
    document = uuid4()
    return [
        _evidence(document, 0, "unrelated grocery list", 0.9),
        _evidence(document, 1, "the security review of the login flow", 0.8),
        _evidence(document, 2, "login flow diagram", 0.7),
        _evidence(document, 3, "quarterly security spending", 0.6),
    ]


async def test_disabled_stage_returns_the_retrieval_order_unchanged(
    candidates: list[Evidence],
) -> None:
    stage = RerankingStage(None, limits=LIMITS)

    evidence, report = await stage.rerank(QUERY, candidates)

    assert not stage.enabled
    assert evidence == tuple(candidates)
    assert report.status is RerankingStatus.DISABLED
    assert not report.applied


async def test_reranking_keeps_the_top_k_in_provider_order_and_records_retrieval_scores(
    candidates: list[Evidence],
) -> None:
    provider = FakeReranker()
    stage = RerankingStage(provider, limits=LIMITS)

    evidence, report = await stage.rerank(QUERY, candidates)

    assert stage.enabled
    assert report.status is RerankingStatus.APPLIED
    assert report.provider == "fake"
    assert report.model == "fake-reranker-v1"
    assert report.considered == 4
    assert report.kept == 2
    assert report.latency_ms >= 0
    assert [e.chunk_index for e in evidence] == [1, 2]
    best = evidence[0]
    assert best.score == pytest.approx(1.0)
    assert best.metadata["retrieval_score"] == 0.8
    assert best.metadata["retrieval_rank"] == 2
    assert best.metadata["rank"] == 1
    assert best.metadata["reranker"] == "fake"
    assert best.metadata["rerank_model"] == "fake-reranker-v1"
    assert best.metadata["retriever"] == "hybrid"  # untouched
    assert evidence[1].metadata["rank"] == 2
    assert provider.calls == [(QUERY.text, [c.text for c in candidates], 2)]


async def test_fewer_candidates_than_top_k_are_all_kept(candidates: list[Evidence]) -> None:
    stage = RerankingStage(FakeReranker(), limits=RerankingLimits(top_k=10, timeout_seconds=1))

    evidence, report = await stage.rerank(QUERY, candidates[:3])

    assert report.kept == 3
    assert [e.chunk_index for e in evidence] == [1, 2, 0]


async def test_nothing_to_rerank_is_skipped_without_a_call() -> None:
    provider = FakeReranker()

    evidence, report = await RerankingStage(provider, limits=LIMITS).rerank(QUERY, [])

    assert evidence == ()
    assert report.status is RerankingStatus.SKIPPED
    assert provider.calls == []


async def test_a_slow_provider_is_cut_off_and_retrieval_order_stands_in(
    candidates: list[Evidence],
) -> None:
    stage = RerankingStage(FakeReranker(delay_seconds=5.0), limits=LIMITS)

    started = asyncio.get_running_loop().time()
    evidence, report = await stage.rerank(QUERY, candidates)
    elapsed = asyncio.get_running_loop().time() - started

    assert elapsed < 2.0  # bounded by the 0.2 s timeout, not the provider's 5 s
    assert report.status is RerankingStatus.DEGRADED
    assert report.error_code == "RERANKER_UNAVAILABLE"
    assert evidence == tuple(candidates)
    assert report.kept == 4


@pytest.mark.parametrize(
    "failure",
    [
        RerankerUnavailableError(),
        RerankerResponseInvalidError(diagnostics="garbage", provider="fake"),
    ],
)
async def test_provider_failures_degrade_instead_of_failing_the_question(
    candidates: list[Evidence], failure: Exception
) -> None:
    provider = FakeReranker(failures=[failure])
    stage = RerankingStage(provider, limits=LIMITS)

    evidence, report = await stage.rerank(QUERY, candidates)

    assert report.status is RerankingStatus.DEGRADED
    assert report.error_code == getattr(failure, "code")  # noqa: B009 - the base is Exception
    assert evidence == tuple(candidates)

    # The next call succeeds again: degradation is per call, not sticky.
    evidence, report = await stage.rerank(QUERY, candidates)
    assert report.status is RerankingStatus.APPLIED


@pytest.mark.parametrize(
    "scripted",
    [
        [RerankScore(index=7, score=1.0)],  # out of range
        [RerankScore(index=1, score=1.0), RerankScore(index=1, score=0.5)],  # repeated
        [RerankScore(index=1, score=float("nan"))],  # not finite
    ],
)
async def test_malformed_provider_answers_degrade(
    candidates: list[Evidence], scripted: list[RerankScore]
) -> None:
    stage = RerankingStage(FakeReranker(scripted=scripted), limits=LIMITS)

    evidence, report = await stage.rerank(QUERY, candidates)

    assert report.status is RerankingStatus.DEGRADED
    assert report.error_code == "RERANKER_RESPONSE_INVALID"
    assert evidence == tuple(candidates)


async def test_a_provider_answering_more_than_top_k_is_cut_to_top_k(
    candidates: list[Evidence],
) -> None:
    scripted = [RerankScore(index=i, score=1.0 - i / 10) for i in range(4)]
    stage = RerankingStage(FakeReranker(scripted=scripted), limits=LIMITS)

    evidence, report = await stage.rerank(QUERY, candidates)

    assert report.kept == 2
    assert [e.chunk_index for e in evidence] == [0, 1]


async def test_programming_errors_are_not_swallowed(candidates: list[Evidence]) -> None:
    stage = RerankingStage(FakeReranker(failures=[TypeError("bug")]), limits=LIMITS)

    with pytest.raises(TypeError):
        await stage.rerank(QUERY, candidates)


def test_validation_and_application_are_deterministic(candidates: list[Evidence]) -> None:
    scores = [RerankScore(index=2, score=0.5), RerankScore(index=1, score=0.5)]

    ranking = validate_scores(scores, count=4, top_k=5)
    assert [s.index for s in ranking] == [1, 2]  # equal scores: lower index first

    reranked = apply_reranking(candidates, ranking, provider="p", model="m")
    assert [e.chunk_index for e in reranked] == [1, 2]
    assert reranked == apply_reranking(candidates, ranking, provider="p", model="m")
    with pytest.raises(ValueError, match="positive"):
        RerankingLimits(top_k=0)
    with pytest.raises(ValueError, match="timeout"):
        RerankingLimits(timeout_seconds=0)


def test_the_fake_scores_by_word_overlap() -> None:
    assert word_overlap("login flow", "the login flow diagram") == 1.0
    assert 0 < word_overlap("login flow", "flow chart") < 1
    assert word_overlap("the invoice", "the") < word_overlap("the invoice", "invoice")
    assert word_overlap("login flow", "nothing") == 0.0
    assert word_overlap("?!", "anything") == 0.0


async def test_an_empty_provider_answer_degrades_instead_of_dropping_all_evidence(
    candidates: list[Evidence],
) -> None:
    stage = RerankingStage(FakeReranker(scripted=[]), limits=LIMITS)

    evidence, report = await stage.rerank(QUERY, candidates)

    assert report.status is RerankingStatus.DEGRADED
    assert report.error_code == "RERANKER_RESPONSE_INVALID"
    assert evidence == tuple(candidates)


async def test_any_dependency_outage_from_the_provider_degrades(
    candidates: list[Evidence],
) -> None:
    stage = RerankingStage(FakeReranker(failures=[DependencyUnavailableError()]), limits=LIMITS)

    evidence, report = await stage.rerank(QUERY, candidates)

    assert report.status is RerankingStatus.DEGRADED
    assert report.error_code == "DEPENDENCY_UNAVAILABLE"
    assert evidence == tuple(candidates)
