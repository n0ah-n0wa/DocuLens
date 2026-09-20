"""The fake provider is deterministic, honours the port contract and can be scripted."""

import math

import pytest

from doculens.application.embeddings import EmbeddingLimits, EmbeddingProvider
from doculens.domain.embeddings import EmbeddingInputError, EmbeddingProviderUnavailableError
from doculens.testing.embeddings import FakeEmbeddingProvider, hashed_vector

pytestmark = pytest.mark.unit


def test_vectors_are_deterministic_unit_length_and_text_specific() -> None:
    first = hashed_vector("hello", 16)
    again = hashed_vector("hello", 16)
    other = hashed_vector("hello!", 16)

    assert first == again
    assert first != other
    assert len(first) == 16
    assert math.isclose(math.sqrt(sum(v * v for v in first)), 1.0, rel_tol=1e-9)
    assert hashed_vector("hello", 16, salt="query") != first


async def test_documents_and_queries_are_embedded_with_usage_recorded() -> None:
    provider: EmbeddingProvider = FakeEmbeddingProvider(dimensions=4)

    documents = await provider.embed_documents(["alpha", "beta", "alpha"])
    query = await provider.embed_query("alpha")

    assert documents.dimensions == 4
    assert len(documents.vectors) == 3
    assert documents.vectors[0] == documents.vectors[2]
    assert documents.vectors[0] != documents.vectors[1]
    assert documents.model == "fake-embedding-v1"
    assert documents.usage.requests == 1
    assert documents.usage.tokens == int(len("alphabetaalpha") * 0.25)
    assert query.single != documents.vectors[0]
    assert query.usage.requests == 1
    assert provider.name == "fake"


async def test_batches_are_counted_as_requests_and_an_empty_input_costs_nothing() -> None:
    provider = FakeEmbeddingProvider(
        limits=EmbeddingLimits(max_batch_size=2, max_batch_characters=100, max_input_characters=50)
    )

    result = await provider.embed_documents(["a", "b", "c", "d", "e"])
    empty = await provider.embed_documents([])

    assert result.usage.requests == 3
    assert len(result.vectors) == 5
    assert empty.vectors == ()
    assert empty.usage.requests == 0
    assert provider.document_calls == [["a", "b", "c", "d", "e"], []]


async def test_scripted_failures_are_raised_in_order_then_cleared() -> None:
    provider = FakeEmbeddingProvider(failures=[EmbeddingProviderUnavailableError()])

    with pytest.raises(EmbeddingProviderUnavailableError):
        await provider.embed_query("q")
    result = await provider.embed_query("q")

    assert result.dimensions == 8
    assert provider.query_calls == ["q"]


async def test_invalid_inputs_are_refused_without_a_call() -> None:
    provider = FakeEmbeddingProvider()

    with pytest.raises(EmbeddingInputError):
        await provider.embed_documents(["ok", " "])
    with pytest.raises(EmbeddingInputError):
        await provider.embed_query("")
    assert provider.document_calls == []
    assert provider.query_calls == []
