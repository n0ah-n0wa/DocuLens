"""Embedding configuration and the provider factory: safe locally, strict when deployed."""

import pytest
from pydantic import SecretStr, ValidationError

from doculens.infrastructure.config import (
    CoreSettings,
    EmbeddingProviderKind,
    Environment,
    LLMProviderKind,
    StorageBackend,
    StorageEncryption,
)
from doculens.infrastructure.embeddings import (
    OpenAICompatibleEmbeddingProvider,
    build_embedding_limits,
    build_embedding_provider,
)
from doculens.testing.embeddings import FakeEmbeddingProvider

pytestmark = pytest.mark.unit

DB_URL = SecretStr("postgresql+asyncpg://u:p@127.0.0.1:1/doculens")
DEPLOYED = {
    "app_env": Environment.PRODUCTION,
    "storage_backend": StorageBackend.S3,
    "storage_bucket": "doculens-prod-documents",
    "storage_region": "eu-central-1",
    "storage_encryption": StorageEncryption.AES256,
    "chroma_url": "https://chroma.internal:8000",
}


def _settings(**overrides: object) -> CoreSettings:
    return CoreSettings(_env_file=None, database_url=DB_URL, **overrides)  # type: ignore[arg-type]


def test_the_fake_provider_is_the_local_default() -> None:
    settings = _settings()

    assert settings.embedding_provider is EmbeddingProviderKind.FAKE
    assert settings.embedding_api_key is None
    provider = build_embedding_provider(settings)
    assert isinstance(provider, FakeEmbeddingProvider)
    assert provider.model == settings.embedding_model
    limits = build_embedding_limits(settings)
    assert (limits.max_batch_size, limits.max_input_characters) == (64, 32_000)


async def test_the_openai_compatible_provider_is_built_from_settings_without_network() -> None:
    settings = _settings(
        embedding_provider=EmbeddingProviderKind.OPENAI,
        embedding_model="nomic-embed-text",
        embedding_api_base_url="http://localhost:11434/v1",
        embedding_dimensions=768,
    )

    provider = build_embedding_provider(settings)

    assert isinstance(provider, OpenAICompatibleEmbeddingProvider)
    assert provider.model == "nomic-embed-text"
    assert provider.name == "openai-compatible"
    await provider.aclose()


@pytest.mark.parametrize(
    ("overrides", "problem"),
    [
        (
            {"embedding_max_input_characters": 10, "embedding_max_batch_characters": 5},
            "must not exceed EMBEDDING_MAX_BATCH",
        ),
        (
            {"embedding_backoff_base_seconds": 9.0, "embedding_backoff_max_seconds": 8.0},
            "BACKOFF_BASE",
        ),
        ({"embedding_api_base_url": "ftp://x"}, "http:// or https://"),
        ({"embedding_api_base_url": "https://user:pw@host/v1"}, "must not embed credentials"),
        ({"embedding_max_attempts": 0}, "embedding_max_attempts"),
    ],
)
def test_incoherent_embedding_settings_are_rejected(
    overrides: dict[str, object], problem: str
) -> None:
    with pytest.raises(ValidationError, match=problem):
        _settings(**overrides)


def test_deployed_environments_refuse_the_fake_provider_and_plain_http() -> None:
    with pytest.raises(ValidationError, match="EMBEDDING_PROVIDER must not be fake"):
        _settings(**DEPLOYED, llm_provider=LLMProviderKind.OPENAI)

    with pytest.raises(ValidationError, match="EMBEDDING_API_BASE_URL must use https"):
        _settings(
            **DEPLOYED,
            embedding_provider=EmbeddingProviderKind.OPENAI,
            llm_provider=LLMProviderKind.OPENAI,
            embedding_api_base_url="http://internal/v1",
        )

    settings = _settings(
        **DEPLOYED,
        embedding_provider=EmbeddingProviderKind.OPENAI,
        llm_provider=LLMProviderKind.OPENAI,
        embedding_api_key=SecretStr("sk-production"),
    )
    assert settings.embedding_provider is EmbeddingProviderKind.OPENAI
    assert "sk-production" not in repr(settings)
