"""Reranker composition from settings (§19, §73; the hosted vendor is OQ-17)."""

from doculens.application.reranking import RerankerProvider, RerankingStage
from doculens.domain.reranking import RerankingLimits
from doculens.infrastructure.config import CoreSettings, RerankerKind
from doculens.testing.reranking import FakeReranker


def build_reranking_limits(settings: CoreSettings) -> RerankingLimits:
    return RerankingLimits(
        top_k=settings.rerank_top_k, timeout_seconds=settings.rerank_timeout_seconds
    )


def build_reranker(settings: CoreSettings) -> RerankerProvider | None:
    """The provider selected by ``RERANKER_PROVIDER``; ``none`` disables the stage."""
    if settings.reranker_provider is RerankerKind.NONE:
        return None
    return FakeReranker(model_name=settings.rerank_model or "fake-reranker-v1")


def build_reranking_stage(settings: CoreSettings) -> RerankingStage:
    return RerankingStage(build_reranker(settings), limits=build_reranking_limits(settings))


__all__ = ["build_reranker", "build_reranking_limits", "build_reranking_stage"]
