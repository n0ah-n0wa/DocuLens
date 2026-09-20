"""Deterministic fake ``EmbeddingProvider`` for tests and local development (OQ-17).

Vectors are derived from a hash of the text, so equal texts always get equal vectors and the
output is stable across runs and machines; nothing is learnt about meaning. Calls are recorded
and failures can be scripted so use cases can be tested against provider behaviour.
"""

import hashlib
import math
from collections.abc import Sequence
from dataclasses import dataclass, field

from doculens.application.embeddings import EmbeddingLimits, batched, validate_inputs
from doculens.domain.embeddings import EmbeddingResult, EmbeddingUsage, Vector

DEFAULT_LIMITS = EmbeddingLimits(
    max_batch_size=64, max_batch_characters=200_000, max_input_characters=32_000
)


def hashed_vector(text: str, dimensions: int, *, salt: str = "") -> Vector:
    """A unit-length vector from the SHA-256 stream of ``salt + text``."""
    values: list[float] = []
    counter = 0
    while len(values) < dimensions:
        digest = hashlib.sha256(f"{salt}\x00{text}\x00{counter}".encode()).digest()
        values.extend((byte / 127.5) - 1.0 for byte in digest)
        counter += 1
    raw = values[:dimensions]
    norm = math.sqrt(sum(value * value for value in raw)) or 1.0
    return tuple(value / norm for value in raw)


@dataclass
class FakeEmbeddingProvider:
    """Records every call; ``failures`` are raised (and consumed) before any embedding happens."""

    dimensions: int = 8
    model_name: str = "fake-embedding-v1"
    limits: EmbeddingLimits = DEFAULT_LIMITS
    tokens_per_character: float = 0.25
    failures: list[Exception] = field(default_factory=list)
    document_calls: list[list[str]] = field(default_factory=list)
    query_calls: list[str] = field(default_factory=list)

    name = "fake"

    @property
    def model(self) -> str:
        return self.model_name

    async def embed_documents(self, texts: Sequence[str]) -> EmbeddingResult:
        validate_inputs(texts, self.limits)
        self._maybe_fail()
        self.document_calls.append(list(texts))
        vectors: list[Vector] = []
        usage = EmbeddingUsage()
        for _, batch in batched(texts, self.limits):
            vectors.extend(hashed_vector(text, self.dimensions) for text in batch)
            usage += EmbeddingUsage(
                requests=1,
                tokens=int(sum(len(text) for text in batch) * self.tokens_per_character),
                latency_ms=1.0,
            )
        return EmbeddingResult(
            vectors=tuple(vectors), model=self.model, dimensions=self.dimensions, usage=usage
        )

    async def embed_query(self, text: str) -> EmbeddingResult:
        validate_inputs([text], self.limits)
        self._maybe_fail()
        self.query_calls.append(text)
        # Queries are embedded in the same space as documents, with a distinct salt so that a
        # query never accidentally equals a document vector byte for byte.
        return EmbeddingResult(
            vectors=(hashed_vector(text, self.dimensions, salt="query"),),
            model=self.model,
            dimensions=self.dimensions,
            usage=EmbeddingUsage(
                requests=1, tokens=int(len(text) * self.tokens_per_character), latency_ms=1.0
            ),
        )

    def _maybe_fail(self) -> None:
        if self.failures:
            raise self.failures.pop(0)
