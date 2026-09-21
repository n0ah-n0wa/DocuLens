"""Reranking port and stage (SPECIFICATIONS.md §19, §67, §73).

:class:`RerankerProvider` is the vendor-free port; :class:`RerankingStage` is the optional §17
stage between candidate selection and context assembly. The stage is fail-open: with no
provider it is disabled, and when the provider times out, is unavailable or answers with
something unusable, the retrieval order stands in and the outcome is reported, so a reranker
outage never turns into a failed question. Every call is bounded by the configured timeout.
"""

import asyncio
import logging
import time
from collections.abc import Sequence
from typing import Protocol

from doculens.domain.errors import DependencyUnavailableError
from doculens.domain.reranking import (
    RerankerError,
    RerankerUnavailableError,
    RerankingLimits,
    RerankingReport,
    RerankingStatus,
    RerankScore,
    apply_reranking,
    validate_scores,
)
from doculens.domain.retrieval import Evidence, PreparedQuery

logger = logging.getLogger(__name__)


class RerankerProvider(Protocol):
    @property
    def name(self) -> str:
        """Stable provider identifier for logs, metrics and evidence metadata."""
        ...

    @property
    def model(self) -> str: ...

    async def rerank(
        self, query: str, documents: Sequence[str], *, top_n: int
    ) -> Sequence[RerankScore]:
        """Relevance of each document to the query, best first, at most ``top_n`` entries.

        Raises ``RerankerUnavailableError`` for transient failures (after the adapter's own
        bounded retries) and ``RerankerError`` subclasses for permanent ones.
        """
        ...


class RerankingStage:
    def __init__(self, provider: RerankerProvider | None, *, limits: RerankingLimits) -> None:
        self._provider = provider
        self._limits = limits

    @property
    def enabled(self) -> bool:
        return self._provider is not None

    async def rerank(
        self, query: PreparedQuery, evidence: Sequence[Evidence]
    ) -> tuple[tuple[Evidence, ...], RerankingReport]:
        """The final evidence set and what happened; never raises for provider trouble."""
        if self._provider is None:
            return tuple(evidence), RerankingReport(status=RerankingStatus.DISABLED)
        provider = self._provider
        if not evidence:
            return (), RerankingReport(
                status=RerankingStatus.SKIPPED, provider=provider.name, model=provider.model
            )
        started = time.perf_counter()
        try:
            async with asyncio.timeout(self._limits.timeout_seconds):
                scores = await provider.rerank(
                    query.text, [item.text for item in evidence], top_n=self._limits.top_k
                )
            ranking = validate_scores(
                scores,
                count=len(evidence),
                top_k=self._limits.top_k,
                provider=provider.name,
                model=provider.model,
            )
        except TimeoutError:
            return self._degraded(evidence, provider, started, RerankerUnavailableError())
        except (RerankerError, DependencyUnavailableError) as exc:
            return self._degraded(evidence, provider, started, exc)
        reranked = apply_reranking(evidence, ranking, provider=provider.name, model=provider.model)
        return reranked, RerankingReport(
            status=RerankingStatus.APPLIED,
            provider=provider.name,
            model=provider.model,
            considered=len(evidence),
            kept=len(reranked),
            latency_ms=_elapsed_ms(started),
        )

    def _degraded(
        self,
        evidence: Sequence[Evidence],
        provider: RerankerProvider,
        started: float,
        error: RerankerError | DependencyUnavailableError,
    ) -> tuple[tuple[Evidence, ...], RerankingReport]:
        latency = _elapsed_ms(started)
        logger.warning(
            "reranking degraded to retrieval order",
            extra={
                "operation": "retrieval.rerank_degraded",
                "provider": provider.name,
                "model": provider.model,
                "error_code": error.code,
                "diagnostics": getattr(error, "diagnostics", None),
                "latency_ms": latency,
            },
        )
        return tuple(evidence), RerankingReport(
            status=RerankingStatus.DEGRADED,
            provider=provider.name,
            model=provider.model,
            considered=len(evidence),
            kept=len(evidence),
            latency_ms=latency,
            error_code=error.code,
        )


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


__all__ = ["RerankerProvider", "RerankingStage"]
