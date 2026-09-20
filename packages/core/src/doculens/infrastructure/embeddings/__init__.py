"""Embedding provider adapters and their composition from settings (§15, §73)."""

from doculens.application.embeddings import EmbeddingLimits, EmbeddingProvider
from doculens.infrastructure.config import CoreSettings, EmbeddingProviderKind
from doculens.infrastructure.embeddings.openai_compatible import (
    OpenAICompatibleConfig,
    OpenAICompatibleEmbeddingProvider,
    RetryPolicy,
)
from doculens.testing.embeddings import FakeEmbeddingProvider


def build_embedding_limits(settings: CoreSettings) -> EmbeddingLimits:
    return EmbeddingLimits(
        max_batch_size=settings.embedding_max_batch_size,
        max_batch_characters=settings.embedding_max_batch_characters,
        max_input_characters=settings.embedding_max_input_characters,
    )


def build_embedding_provider(settings: CoreSettings) -> EmbeddingProvider:
    """The provider selected by ``EMBEDDING_PROVIDER``; the fake is refused when deployed."""
    limits = build_embedding_limits(settings)
    if settings.embedding_provider is EmbeddingProviderKind.FAKE:
        return FakeEmbeddingProvider(
            dimensions=settings.embedding_dimensions or 8,
            model_name=settings.embedding_model,
            limits=limits,
        )
    key = settings.embedding_api_key
    return OpenAICompatibleEmbeddingProvider(
        OpenAICompatibleConfig(
            base_url=settings.embedding_api_base_url,
            api_key=key.get_secret_value() if key is not None else None,
            model=settings.embedding_model,
            dimensions=settings.embedding_dimensions,
            timeout_seconds=settings.embedding_timeout_seconds,
            limits=limits,
            retry=RetryPolicy(
                max_attempts=settings.embedding_max_attempts,
                base_delay_seconds=settings.embedding_backoff_base_seconds,
                max_delay_seconds=settings.embedding_backoff_max_seconds,
            ),
            max_concurrency=settings.embedding_max_concurrency,
        )
    )


__all__ = [
    "OpenAICompatibleConfig",
    "OpenAICompatibleEmbeddingProvider",
    "RetryPolicy",
    "build_embedding_limits",
    "build_embedding_provider",
]
