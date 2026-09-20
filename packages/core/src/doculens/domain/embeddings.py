"""Embedding concepts (SPECIFICATIONS.md §15, §38, §73).

Vectors, the usage every call must account for, and the structured failures a provider can
report. Nothing here knows a vendor: the port in ``doculens.application.embeddings`` and the
adapters in the infrastructure layer share these types.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Self

from doculens.domain.errors import DependencyUnavailableError, DomainError, InvalidInputError

Vector = tuple[float, ...]


class EmbeddingError(DomainError):
    """Base of every embedding failure; carries where it happened for logs and metrics."""

    code = "EMBEDDING_ERROR"
    default_message = "The embedding request could not be completed."

    def __init__(  # noqa: PLR0913 - the structured fields every embedding error carries
        self,
        message: str | None = None,
        *,
        provider: str | None = None,
        model: str | None = None,
        status_code: int | None = None,
        attempts: int = 1,
        retry_after_seconds: float | None = None,
        diagnostics: str | None = None,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.model = model
        self.status_code = status_code
        self.attempts = attempts
        self.retry_after_seconds = retry_after_seconds
        self.diagnostics = diagnostics


class EmbeddingInputError(InvalidInputError):
    """The caller's input cannot be embedded (empty text, over the size limit)."""

    code = "EMBEDDING_INPUT_INVALID"
    default_message = "The text cannot be embedded."


class EmbeddingProviderUnavailableError(DependencyUnavailableError, EmbeddingError):
    """Transient: the provider timed out, rate-limited or failed and retries are exhausted."""

    code = "EMBEDDING_PROVIDER_UNAVAILABLE"
    default_message = "The embedding service is temporarily unavailable."


class EmbeddingRateLimitedError(EmbeddingProviderUnavailableError):
    code = "EMBEDDING_RATE_LIMITED"
    default_message = "The embedding service is rate limiting requests."


class EmbeddingRequestRejectedError(EmbeddingError):
    """Permanent for this request: the provider refused it (authentication, model, payload)."""

    code = "EMBEDDING_REQUEST_REJECTED"
    default_message = "The embedding service rejected the request."


class EmbeddingResponseInvalidError(EmbeddingError):
    """The provider answered with something that is not a usable embedding response."""

    code = "EMBEDDING_RESPONSE_INVALID"
    default_message = "The embedding service returned an unusable response."


@dataclass(frozen=True, slots=True)
class EmbeddingUsage:
    """What one or more provider calls cost (§38); ``tokens`` is None when not reported."""

    requests: int = 0
    tokens: int | None = None
    latency_ms: float = 0.0

    def __add__(self, other: "EmbeddingUsage") -> "EmbeddingUsage":
        tokens: int | None
        if self.tokens is None and other.tokens is None:
            tokens = None
        else:
            tokens = (self.tokens or 0) + (other.tokens or 0)
        return EmbeddingUsage(
            requests=self.requests + other.requests,
            tokens=tokens,
            latency_ms=self.latency_ms + other.latency_ms,
        )


@dataclass(frozen=True, slots=True)
class EmbeddingResult:
    """Vectors in the order of the inputs, the model that produced them, and the usage."""

    vectors: tuple[Vector, ...]
    model: str
    dimensions: int
    usage: EmbeddingUsage = EmbeddingUsage()

    @classmethod
    def build(
        cls, vectors: Sequence[Sequence[float]], *, model: str, usage: EmbeddingUsage
    ) -> Self:
        """Validate shape and values: same dimension everywhere, finite numbers only."""
        frozen = tuple(tuple(float(value) for value in vector) for vector in vectors)
        dimensions = len(frozen[0]) if frozen else 0
        for vector in frozen:
            if len(vector) != dimensions or dimensions == 0:
                message = "embedding vectors have inconsistent or zero dimensions"
                raise EmbeddingResponseInvalidError(diagnostics=message, model=model)
            if any(not math.isfinite(value) for value in vector):
                message = "embedding vector contains a non-finite value"
                raise EmbeddingResponseInvalidError(diagnostics=message, model=model)
        return cls(vectors=frozen, model=model, dimensions=dimensions, usage=usage)

    @property
    def single(self) -> Vector:
        if len(self.vectors) != 1:
            message = f"expected one vector, got {len(self.vectors)}"
            raise EmbeddingResponseInvalidError(diagnostics=message, model=self.model)
        return self.vectors[0]


__all__ = [
    "EmbeddingError",
    "EmbeddingInputError",
    "EmbeddingProviderUnavailableError",
    "EmbeddingRateLimitedError",
    "EmbeddingRequestRejectedError",
    "EmbeddingResponseInvalidError",
    "EmbeddingResult",
    "EmbeddingUsage",
    "Vector",
]
