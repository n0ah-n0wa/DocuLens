"""Unit tests for asyncpg SSL connect_args selection."""

import pytest
from pydantic import SecretStr

from doculens.infrastructure.config import CoreSettings, Environment
from doculens.infrastructure.persistence.database import _asyncpg_connect_args

pytestmark = pytest.mark.unit

DEPLOYED_BASE = {
    "storage_backend": "s3",
    "storage_bucket": "doculens-prod-documents",
    "storage_region": "eu-central-1",
    "storage_encryption": "AES256",
    "embedding_provider": "openai",
    "llm_provider": "openai",
    "chroma_url": "https://chroma.internal:8000",
    "queue_backend": "sqs",
    "queue_sqs_url": "https://sqs.eu-central-1.amazonaws.com/123456789012/doculens",
    "queue_sqs_dlq_url": "https://sqs.eu-central-1.amazonaws.com/123456789012/doculens-dlq",
    "queue_sqs_region": "eu-central-1",
    "rate_limit_backend": "redis",
    "redis_url": "rediss://redis.internal:6379/0",
    "storage_expected_bucket_owner": "123456789012",
    "chroma_api_token": SecretStr("chroma-deployed-token"),
}


def test_local_without_ssl_query_skips_connect_args() -> None:
    settings = CoreSettings(
        _env_file=None,
        database_url=SecretStr("postgresql+asyncpg://u:p@127.0.0.1:5432/doculens"),
        app_env=Environment.LOCAL,
    )
    assert _asyncpg_connect_args(settings) == {}


def test_ssl_require_in_url_enables_ssl_for_local() -> None:
    settings = CoreSettings(
        _env_file=None,
        database_url=SecretStr("postgresql+asyncpg://u:p@db:5432/doculens?ssl=require"),
        app_env=Environment.LOCAL,
    )
    assert _asyncpg_connect_args(settings) == {"ssl": True}


@pytest.mark.parametrize("environment", [Environment.STAGING, Environment.PRODUCTION])
def test_deployed_environments_enable_ssl(environment: Environment) -> None:
    settings = CoreSettings(
        _env_file=None,
        database_url=SecretStr("postgresql+asyncpg://u:p@db:5432/doculens"),
        app_env=environment,
        **DEPLOYED_BASE,  # type: ignore[arg-type]
    )
    assert _asyncpg_connect_args(settings) == {"ssl": True}
