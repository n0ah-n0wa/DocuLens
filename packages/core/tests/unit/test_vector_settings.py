"""Vector store configuration and factory: chroma by default, memory for unit tests only."""

import pytest
from pydantic import SecretStr, ValidationError

from doculens.infrastructure.config import (
    CoreSettings,
    EmbeddingProviderKind,
    Environment,
    LLMProviderKind,
    StorageBackend,
    StorageEncryption,
    VectorStoreKind,
)
from doculens.infrastructure.vectors import ChromaVectorStore, build_vector_store
from doculens.testing.vectors import InMemoryVectorStore

pytestmark = pytest.mark.unit

DB_URL = SecretStr("postgresql+asyncpg://u:p@127.0.0.1:1/doculens")
DEPLOYED = {
    "app_env": Environment.PRODUCTION,
    "storage_backend": StorageBackend.S3,
    "storage_bucket": "doculens-prod-documents",
    "storage_region": "eu-central-1",
    "storage_encryption": StorageEncryption.AES256,
    "embedding_provider": EmbeddingProviderKind.OPENAI,
    "llm_provider": LLMProviderKind.OPENAI,
}


def _settings(**overrides: object) -> CoreSettings:
    return CoreSettings(_env_file=None, database_url=DB_URL, **overrides)  # type: ignore[arg-type]


def test_chroma_is_the_default_and_the_collection_follows_the_embedding_model() -> None:
    settings = _settings()

    assert settings.vector_store is VectorStoreKind.CHROMA
    assert settings.chroma_url == "http://localhost:8001"
    store = build_vector_store(settings)
    assert isinstance(store, ChromaVectorStore)
    assert store.collection == "doculens-text-embedding-3-small"


def test_the_memory_store_is_built_for_tests() -> None:
    store = build_vector_store(_settings(vector_store=VectorStoreKind.MEMORY, embedding_model="m"))

    assert isinstance(store, InMemoryVectorStore)
    assert store.collection == "doculens-m"


@pytest.mark.parametrize(
    ("overrides", "problem"),
    [
        ({"chroma_url": "chroma:8000"}, "http:// or https://"),
        ({"chroma_url": "http://user:pw@chroma:8000"}, "must not embed credentials"),
        ({"chroma_collection_prefix": "Bad Prefix"}, "chroma_collection_prefix"),
        ({"vector_search_max_results": 0}, "vector_search_max_results"),
    ],
)
def test_incoherent_vector_settings_are_rejected(
    overrides: dict[str, object], problem: str
) -> None:
    with pytest.raises(ValidationError, match=problem):
        _settings(**overrides)


def test_deployed_environments_require_chroma_over_https() -> None:
    with pytest.raises(ValidationError, match="VECTOR_STORE must be chroma"):
        _settings(**DEPLOYED, vector_store=VectorStoreKind.MEMORY, chroma_url="https://c")
    with pytest.raises(ValidationError, match="CHROMA_URL must use https"):
        _settings(**DEPLOYED, chroma_url="http://chroma.internal:8000")

    settings = _settings(
        **DEPLOYED, chroma_url="https://chroma.internal:8000", chroma_api_token=SecretStr("t")
    )
    assert settings.vector_store is VectorStoreKind.CHROMA
    assert "SecretStr" in repr(settings.chroma_api_token)
