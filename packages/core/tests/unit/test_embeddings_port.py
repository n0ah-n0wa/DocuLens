"""The embedding port's shared rules: results, usage, limits, batching, input validation."""

import math

import pytest

from doculens.application.embeddings import EmbeddingLimits, batched, validate_inputs
from doculens.domain.embeddings import (
    EmbeddingInputError,
    EmbeddingProviderUnavailableError,
    EmbeddingRateLimitedError,
    EmbeddingResponseInvalidError,
    EmbeddingResult,
    EmbeddingUsage,
)
from doculens.domain.errors import DependencyUnavailableError

pytestmark = pytest.mark.unit

LIMITS = EmbeddingLimits(max_batch_size=3, max_batch_characters=20, max_input_characters=10)


def test_results_validate_shape_and_values() -> None:
    result = EmbeddingResult.build(
        [[0.1, 0.2], [0.3, 0.4]], model="m", usage=EmbeddingUsage(requests=1, tokens=5)
    )

    assert result.dimensions == 2
    assert result.vectors == ((0.1, 0.2), (0.3, 0.4))
    assert result.usage.tokens == 5
    with pytest.raises(EmbeddingResponseInvalidError):
        EmbeddingResult.build([[0.1, 0.2], [0.3]], model="m", usage=EmbeddingUsage())
    with pytest.raises(EmbeddingResponseInvalidError):
        EmbeddingResult.build([[]], model="m", usage=EmbeddingUsage())
    with pytest.raises(EmbeddingResponseInvalidError):
        EmbeddingResult.build([[math.nan, 1.0]], model="m", usage=EmbeddingUsage())
    with pytest.raises(EmbeddingResponseInvalidError):
        EmbeddingResult.build([[1.0], [2.0]], model="m", usage=EmbeddingUsage()).single  # noqa: B018 - property raises


def test_usage_adds_up_and_keeps_unknown_tokens_unknown() -> None:
    known = EmbeddingUsage(requests=1, tokens=10, latency_ms=5.0)
    unknown = EmbeddingUsage(requests=1, tokens=None, latency_ms=2.5)

    assert known + known == EmbeddingUsage(requests=2, tokens=20, latency_ms=10.0)
    assert unknown + unknown == EmbeddingUsage(requests=2, tokens=None, latency_ms=5.0)
    assert known + unknown == EmbeddingUsage(requests=2, tokens=10, latency_ms=7.5)


def test_batches_are_bounded_by_count_and_characters_and_keep_order() -> None:
    texts = ["aaaa", "bbbb", "cccc", "dddd", "eeeeeeeeee", "ff", "gg", "hh", "ii"]

    batches = list(batched(texts, LIMITS))

    assert batches == [
        (0, ["aaaa", "bbbb", "cccc"]),  # three items
        (3, ["dddd", "eeeeeeeeee", "ff"]),  # 16 characters, three items
        (6, ["gg", "hh", "ii"]),
    ]
    assert list(batched(["x" * 15, "y" * 10, "z"], LIMITS)) == [
        (0, ["x" * 15]),  # 15 + 10 would exceed 20 characters
        (1, ["y" * 10, "z"]),
    ]
    assert list(batched([], LIMITS)) == []


def test_inputs_are_validated_before_any_request() -> None:
    validate_inputs(["fine", "also fine"], LIMITS)
    with pytest.raises(EmbeddingInputError):
        validate_inputs(["fine", ""], LIMITS)
    with pytest.raises(EmbeddingInputError):
        validate_inputs(["   "], LIMITS)
    with pytest.raises(EmbeddingInputError):
        validate_inputs(["x" * 11], LIMITS)


def test_limits_must_be_coherent() -> None:
    with pytest.raises(ValueError, match="positive"):
        EmbeddingLimits(max_batch_size=0, max_batch_characters=1, max_input_characters=1)
    with pytest.raises(ValueError, match="fit in one batch"):
        EmbeddingLimits(max_batch_size=1, max_batch_characters=5, max_input_characters=6)


def test_errors_are_structured_and_classified() -> None:
    error = EmbeddingRateLimitedError(
        provider="p", model="m", status_code=429, attempts=4, retry_after_seconds=2.0
    )

    assert isinstance(error, EmbeddingProviderUnavailableError)
    assert isinstance(error, DependencyUnavailableError)
    assert error.code == "EMBEDDING_RATE_LIMITED"
    assert (error.provider, error.model, error.status_code, error.attempts) == ("p", "m", 429, 4)
    assert error.retry_after_seconds == 2.0
