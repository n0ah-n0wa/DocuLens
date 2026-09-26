"""Reranker configuration: off by default, the fake for local use only, coherent limits."""

import pytest
from pydantic import SecretStr, ValidationError

from doculens.application.reranking import RerankingStage
from doculens.infrastructure.config import (
    CoreSettings,
    EmbeddingProviderKind,
    Environment,
    LLMProviderKind,
    RerankerKind,
    StorageBackend,
    StorageEncryption,
)
from doculens.infrastructure.reranking import (
    build_reranker,
    build_reranking_limits,
    build_reranking_stage,
)
from doculens.testing.reranking import FakeReranker

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
    "chroma_url": "https://chroma.internal:8000",
    "queue_backend": "sqs",
    "queue_sqs_url": "https://sqs.eu-central-1.amazonaws.com/123456789012/doculens",
    "queue_sqs_dlq_url": "https://sqs.eu-central-1.amazonaws.com/123456789012/doculens-dlq",
    "queue_sqs_region": "eu-central-1",
}


def _settings(**overrides: object) -> CoreSettings:
    return CoreSettings(_env_file=None, database_url=DB_URL, **overrides)  # type: ignore[arg-type]


def test_reranking_is_off_by_default() -> None:
    settings = _settings()

    assert settings.reranker_provider is RerankerKind.NONE
    assert build_reranker(settings) is None
    stage = build_reranking_stage(settings)
    assert isinstance(stage, RerankingStage)
    assert not stage.enabled
    limits = build_reranking_limits(settings)
    assert limits.top_k == 5  # "Top 5 evidence chunks" (§19)
    assert limits.timeout_seconds == 5.0


def test_the_fake_reranker_is_built_for_local_use() -> None:
    settings = _settings(reranker_provider=RerankerKind.FAKE, rerank_model="rerank-test")

    provider = build_reranker(settings)
    assert isinstance(provider, FakeReranker)
    assert provider.model == "rerank-test"
    assert build_reranking_stage(settings).enabled


@pytest.mark.parametrize(
    ("overrides", "problem"),
    [
        ({"rerank_top_k": 50}, "RERANK_TOP_K must not exceed RETRIEVAL_CANDIDATES"),
        ({"rerank_top_k": 0}, "rerank_top_k"),
        ({"rerank_timeout_seconds": 0}, "rerank_timeout_seconds"),
        ({"reranker_provider": "cohere"}, "reranker_provider"),
    ],
)
def test_incoherent_reranking_settings_are_rejected(
    overrides: dict[str, object], problem: str
) -> None:
    with pytest.raises(ValidationError, match=problem):
        _settings(**overrides)


def test_deployed_environments_refuse_the_fake_reranker_but_allow_none() -> None:
    with pytest.raises(ValidationError, match="RERANKER_PROVIDER must not be fake"):
        _settings(**DEPLOYED, reranker_provider=RerankerKind.FAKE)

    assert _settings(**DEPLOYED).reranker_provider is RerankerKind.NONE
