"""Deterministic fake ``RerankerProvider`` for tests and local development (OQ-17).

Scores are the share of the query's words present in each document (word overlap), so tests
can predict the order; calls are recorded, delays and failures can be scripted so the stage's
timeout and degradation paths are exercised without a network.
"""

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from doculens.domain.reranking import RerankScore
from doculens.domain.retrieval import words_of
from doculens.testing.embeddings import word_weight


def word_overlap(query: str, document: str) -> float:
    """The weighted share of the query's distinct words present in the document."""
    terms = set(words_of(query))
    if not terms:
        return 0.0
    words = set(words_of(document))
    total = sum(word_weight(term) for term in terms)
    return sum(word_weight(term) for term in terms & words) / total


@dataclass
class FakeReranker:
    model_name: str = "fake-reranker-v1"
    scorer: Callable[[str, str], float] = word_overlap
    delay_seconds: float = 0.0
    failures: list[Exception] = field(default_factory=list)
    calls: list[tuple[str, list[str], int]] = field(default_factory=list)
    scripted: Sequence[RerankScore] | None = None  # returned verbatim when set

    name = "fake"

    @property
    def model(self) -> str:
        return self.model_name

    async def rerank(
        self, query: str, documents: Sequence[str], *, top_n: int
    ) -> Sequence[RerankScore]:
        self.calls.append((query, list(documents), top_n))
        if self.failures:
            raise self.failures.pop(0)
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        if self.scripted is not None:
            return list(self.scripted)
        scores = [
            RerankScore(index=index, score=self.scorer(query, document))
            for index, document in enumerate(documents)
        ]
        scores.sort(key=lambda score: (-score.score, score.index))
        return scores[:top_n]


__all__ = ["FakeReranker", "word_overlap"]
