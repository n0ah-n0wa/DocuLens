"""Embedding provider port (SPECIFICATIONS.md §15, §38, §73).

Use cases embed chunks and queries through :class:`EmbeddingProvider` and never see a vendor
SDK or HTTP client. Every adapter honours the same contract:

- ``embed_documents`` returns one vector per input, in input order, and ``embed_query`` embeds a
  single retrieval query (providers may treat the two differently);
- inputs are validated with :func:`validate_inputs` before any network call: no empty text and
  a configurable maximum length, so a caller bug never turns into a paid request;
- large inputs are sent in batches (:func:`batched`), bounded by item count and characters;
- failures are the structured errors of ``doculens.domain.embeddings``: transient conditions
  (timeouts, rate limits, 5xx) surface as ``EmbeddingProviderUnavailableError`` only after the
  adapter's bounded retries, permanent rejections and malformed answers as their own types;
- every result carries usage (requests, tokens when reported, latency) for the §38 ledger.
"""

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Protocol

from doculens.domain.embeddings import EmbeddingInputError, EmbeddingResult


@dataclass(frozen=True, slots=True)
class EmbeddingLimits:
    """Bounds every adapter applies; injected from configuration, never hard-coded."""

    max_batch_size: int
    max_batch_characters: int
    max_input_characters: int

    def __post_init__(self) -> None:
        if (
            self.max_batch_size < 1
            or self.max_batch_characters < 1
            or self.max_input_characters < 1
        ):
            message = "embedding limits must be positive"
            raise ValueError(message)
        if self.max_input_characters > self.max_batch_characters:
            message = "a single input must fit in one batch"
            raise ValueError(message)


class EmbeddingProvider(Protocol):
    @property
    def name(self) -> str:
        """Stable provider identifier for logs, metrics and chunk metadata."""
        ...

    @property
    def model(self) -> str:
        """The model every vector of this provider comes from (recorded for §32 staleness)."""
        ...

    async def embed_documents(self, texts: Sequence[str]) -> EmbeddingResult:
        """One vector per text, in order; an empty sequence yields an empty result."""
        ...

    async def embed_query(self, text: str) -> EmbeddingResult:
        """A single vector for a retrieval query."""
        ...


def validate_inputs(texts: Sequence[str], limits: EmbeddingLimits) -> None:
    for index, text in enumerate(texts):
        if not text or not text.strip():
            message = f"input {index} is empty"
            raise EmbeddingInputError(message)
        if len(text) > limits.max_input_characters:
            message = f"input {index} exceeds {limits.max_input_characters} characters"
            raise EmbeddingInputError(message)


def batched(texts: Sequence[str], limits: EmbeddingLimits) -> Iterator[tuple[int, list[str]]]:
    """Consecutive batches ``(offset, texts)`` bounded by item count and total characters."""
    batch: list[str] = []
    characters = 0
    offset = 0
    for text in texts:
        if batch and (
            len(batch) >= limits.max_batch_size
            or characters + len(text) > limits.max_batch_characters
        ):
            yield offset, batch
            offset += len(batch)
            batch, characters = [], 0
        batch.append(text)
        characters += len(text)
    if batch:
        yield offset, batch


__all__ = ["EmbeddingLimits", "EmbeddingProvider", "batched", "validate_inputs"]
