"""Language-model adapters and their composition from settings (§21, §73)."""

from doculens.application.llm import LLMLimits, LLMProvider
from doculens.infrastructure.config import CoreSettings, LLMProviderKind
from doculens.infrastructure.llm.openai_compatible import (
    OpenAICompatibleLLMConfig,
    OpenAICompatibleLLMProvider,
)
from doculens.infrastructure.providers import RetryPolicy
from doculens.testing.llm import FakeLLMProvider


def build_llm_limits(settings: CoreSettings) -> LLMLimits:
    return LLMLimits(
        max_input_characters=settings.llm_max_input_characters,
        max_output_tokens=settings.llm_max_output_tokens,
    )


def build_llm_provider(settings: CoreSettings) -> LLMProvider:
    """The provider selected by ``LLM_PROVIDER``; the fake is refused when deployed."""
    limits = build_llm_limits(settings)
    if settings.llm_provider is LLMProviderKind.FAKE:
        return FakeLLMProvider(model_name=settings.llm_model, limits=limits)
    key = settings.llm_api_key
    return OpenAICompatibleLLMProvider(
        OpenAICompatibleLLMConfig(
            base_url=settings.llm_api_base_url,
            api_key=key.get_secret_value() if key is not None else None,
            model=settings.llm_model,
            timeout_seconds=settings.llm_timeout_seconds,
            limits=limits,
            retry=RetryPolicy(
                max_attempts=settings.llm_max_attempts,
                base_delay_seconds=settings.llm_backoff_base_seconds,
                max_delay_seconds=settings.llm_backoff_max_seconds,
            ),
        )
    )


__all__ = [
    "OpenAICompatibleLLMConfig",
    "OpenAICompatibleLLMProvider",
    "build_llm_limits",
    "build_llm_provider",
]
