"""Reranking concepts (SPECIFICATIONS.md §19, §73).

A reranker scores candidate evidence against the question and the pipeline keeps the best
``top_k`` (§19: "top 20 retrieved chunks → reranker → top 5 evidence chunks"). Everything here
is provider-free: the provider returns :class:`RerankScore` values, :func:`validate_scores`
refuses malformed answers, and :func:`apply_reranking` rewrites the evidence ranking while
keeping the retrieval score and rank in the metadata for citations and evaluation.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from types import MappingProxyType

from doculens.domain.errors import DependencyUnavailableError, DomainError
from doculens.domain.retrieval import Evidence


class RerankerError(DomainError):
    code = "RERANKER_ERROR"
    default_message = "The reranking request could not be completed."

    def __init__(
        self,
        message: str | None = None,
        *,
        provider: str = "",
        model: str = "",
        diagnostics: str | None = None,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.model = model
        self.diagnostics = diagnostics


class RerankerUnavailableError(DependencyUnavailableError):
    """Timeouts, connection failures, rate limits: the retrieval order stands in (§67)."""

    code = "RERANKER_UNAVAILABLE"
    default_message = "The reranking provider is temporarily unavailable."


class RerankerResponseInvalidError(RerankerError):
    code = "RERANKER_RESPONSE_INVALID"
    default_message = "The reranking provider returned an unusable answer."


@dataclass(frozen=True, slots=True)
class RerankScore:
    """The provider's relevance of ``documents[index]`` to the query; higher is better."""

    index: int
    score: float


@dataclass(frozen=True, slots=True)
class RerankingLimits:
    top_k: int = 5
    timeout_seconds: float = 5.0

    def __post_init__(self) -> None:
        if self.top_k < 1:
            message = "top_k must be positive"
            raise ValueError(message)
        if not self.timeout_seconds > 0:
            message = "the reranking timeout must be positive"
            raise ValueError(message)


class RerankingStatus(StrEnum):
    DISABLED = "disabled"  # no provider configured: retrieval order is the final order
    SKIPPED = "skipped"  # nothing to rerank
    APPLIED = "applied"
    DEGRADED = "degraded"  # the provider failed or timed out: retrieval order stands in


@dataclass(frozen=True, slots=True)
class RerankingReport:
    status: RerankingStatus
    provider: str = ""
    model: str = ""
    considered: int = 0
    kept: int = 0
    latency_ms: int = 0
    error_code: str | None = None

    @property
    def applied(self) -> bool:
        return self.status is RerankingStatus.APPLIED


def validate_scores(
    scores: Sequence[RerankScore], *, count: int, top_k: int, provider: str = "", model: str = ""
) -> list[RerankScore]:
    """The provider's answer as a best-first ranking of at most ``top_k`` distinct, in-range,
    finite scores; an empty answer for a non-empty input, or anything else, is malformed."""
    if not scores:
        message = "the provider returned no scores"
        raise RerankerResponseInvalidError(diagnostics=message, provider=provider, model=model)
    seen: set[int] = set()
    for score in scores:
        if not 0 <= score.index < count or score.index in seen:
            message = f"index {score.index} is out of range or repeated"
            raise RerankerResponseInvalidError(diagnostics=message, provider=provider, model=model)
        if not math.isfinite(score.score):
            message = f"score for index {score.index} is not finite"
            raise RerankerResponseInvalidError(diagnostics=message, provider=provider, model=model)
        seen.add(score.index)
    ordered = sorted(scores, key=lambda score: (-score.score, score.index))
    return ordered[:top_k]


def apply_reranking(
    evidence: Sequence[Evidence], scores: Sequence[RerankScore], *, provider: str, model: str
) -> tuple[Evidence, ...]:
    """The final evidence set: the scored candidates in reranked order, renumbered, with the
    retrieval score and rank kept in the metadata."""
    reranked: list[Evidence] = []
    for rank, score in enumerate(scores, 1):
        item = evidence[score.index]
        metadata = dict(item.metadata)
        metadata.update(
            {
                "retrieval_score": item.score,
                "retrieval_rank": metadata.get("rank", score.index + 1),
                "rank": rank,
                "reranker": provider,
                "rerank_model": model,
            }
        )
        reranked.append(replace(item, score=score.score, metadata=MappingProxyType(metadata)))
    return tuple(reranked)


__all__ = [
    "RerankScore",
    "RerankerError",
    "RerankerResponseInvalidError",
    "RerankerUnavailableError",
    "RerankingLimits",
    "RerankingReport",
    "RerankingStatus",
    "apply_reranking",
    "validate_scores",
]
