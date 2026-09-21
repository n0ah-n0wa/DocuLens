"""Language-model configuration: the fake locally, a real adapter when configured, refusals."""

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
from doculens.infrastructure.llm import (
    OpenAICompatibleLLMProvider,
    build_llm_limits,
    build_llm_provider,
)
from doculens.testing.llm import FakeLLMProvider

pytestmark = pytest.mark.unit

DB_URL = SecretStr("postgresql+asyncpg://u:p@127.0.0.1:1/doculens")
DEPLOYED = {
    "app_env": Environment.PRODUCTION,
    "storage_backend": StorageBackend.S3,
    "storage_bucket": "doculens-prod-documents",
    "storage_region": "eu-central-1",
    "storage_encryption": StorageEncryption.AES256,
    "embedding_provider": EmbeddingProviderKind.OPENAI,
    "chroma_url": "https://chroma.internal:8000",
}


def _settings(**overrides: object) -> CoreSettings:
    return CoreSettings(_env_file=None, database_url=DB_URL, **overrides)  # type: ignore[arg-type]


def test_the_fake_is_the_local_default() -> None:
    settings = _settings()

    assert settings.llm_provider is LLMProviderKind.FAKE
    provider = build_llm_provider(settings)
    assert isinstance(provider, FakeLLMProvider)
    assert provider.model == "gpt-4o-mini"
    limits = build_llm_limits(settings)
    assert limits.max_output_tokens == 1024
    assert limits.max_input_characters == 200_000
    assert settings.llm_timeout_seconds == 60.0
    assert settings.llm_max_attempts == 3


def test_the_openai_compatible_adapter_is_built_from_settings() -> None:
    settings = _settings(
        llm_provider=LLMProviderKind.OPENAI,
        llm_model="llama3",
        llm_api_base_url="http://localhost:11434/v1",
        llm_api_key=SecretStr("k"),
    )

    provider = build_llm_provider(settings)

    assert isinstance(provider, OpenAICompatibleLLMProvider)
    assert provider.name == "openai-compatible"
    assert provider.model == "llama3"
    assert "SecretStr" in repr(settings.llm_api_key)
    assert "llm_api_key=SecretStr('**********')" in repr(settings)


@pytest.mark.parametrize(
    ("overrides", "problem"),
    [
        ({"llm_api_base_url": "llm.example.test"}, "LLM_API_BASE_URL must start with"),
        ({"llm_api_base_url": "https://user:pw@llm.example.test"}, "must not embed credentials"),
        (
            {"llm_backoff_base_seconds": 20, "llm_backoff_max_seconds": 5},
            "LLM_BACKOFF_BASE_SECONDS",
        ),
        ({"llm_timeout_seconds": 0}, "llm_timeout_seconds"),
        ({"llm_max_attempts": 0}, "llm_max_attempts"),
        ({"llm_temperature": 3}, "llm_temperature"),
        ({"llm_provider": "anthropic"}, "llm_provider"),
        ({"llm_max_input_characters": 10_000}, "prompt budget"),
        ({"generation_timeout_seconds": 0}, "generation_timeout_seconds"),
    ],
)
def test_incoherent_llm_settings_are_rejected(overrides: dict[str, object], problem: str) -> None:
    with pytest.raises(ValidationError, match=problem):
        _settings(**overrides)


def test_deployed_environments_refuse_the_fake_and_plain_http() -> None:
    with pytest.raises(ValidationError, match="LLM_PROVIDER must not be fake"):
        _settings(**DEPLOYED)
    with pytest.raises(ValidationError, match="LLM_API_BASE_URL must use https"):
        _settings(
            **DEPLOYED, llm_provider=LLMProviderKind.OPENAI, llm_api_base_url="http://llm.internal"
        )

    settings = _settings(**DEPLOYED, llm_provider=LLMProviderKind.OPENAI)
    assert settings.llm_api_base_url == "https://api.openai.com/v1"
