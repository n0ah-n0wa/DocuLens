"""Retrieval and context configuration, and the composition of the RAG boundary."""

import pytest
from pydantic import SecretStr, ValidationError

from doculens.application.rag import RagService
from doculens.application.retrieval import RetrievalService
from doculens.domain.retrieval import RetrievalStrategy
from doculens.infrastructure.config import CoreSettings, EmbeddingProviderKind, VectorStoreKind
from doculens.infrastructure.rag import (
    build_configured_retriever,
    build_context_limits,
    build_rag_service,
    build_retrieval_limits,
    build_retrieval_service,
)
from doculens.testing.fakes import InMemoryStore, InMemoryUnitOfWork

pytestmark = pytest.mark.unit

DB_URL = SecretStr("postgresql+asyncpg://u:p@127.0.0.1:1/doculens")


def _settings(**overrides: object) -> CoreSettings:
    return CoreSettings(_env_file=None, database_url=DB_URL, **overrides)  # type: ignore[arg-type]


def test_defaults_follow_the_specification_examples() -> None:
    limits = build_retrieval_limits(_settings())
    context = build_context_limits(_settings())

    assert limits.candidate_limit == 20  # "Top 20 retrieved chunks" (§19)
    assert limits.max_query_characters == 2_000
    assert limits.min_score == 0.0
    assert limits.max_scope_documents == 100
    assert limits.timeout_seconds == 15.0
    assert context.max_chunks == 5  # "Top 5 evidence chunks" (§19)
    assert context.max_characters == 12_000
    assert context.max_chunks_per_document == 3
    assert _settings().retrieval_strategy is RetrievalStrategy.HYBRID


@pytest.mark.parametrize(
    ("overrides", "problem"),
    [
        ({"retrieval_candidates": 500}, "RETRIEVAL_CANDIDATES must not exceed"),
        ({"context_max_chunks": 30}, "CONTEXT_MAX_CHUNKS must not exceed"),
        (
            {"retrieval_max_query_characters": 50_000},
            "RETRIEVAL_MAX_QUERY_CHARACTERS must not exceed",
        ),
        ({"retrieval_min_score": 2.0}, "retrieval_min_score"),
        ({"context_max_characters": 0}, "context_max_characters"),
        ({"retrieval_strategy": "bm25"}, "retrieval_strategy"),
        ({"retrieval_timeout_seconds": 0}, "retrieval_timeout_seconds"),
    ],
)
def test_incoherent_retrieval_settings_are_rejected(
    overrides: dict[str, object], problem: str
) -> None:
    with pytest.raises(ValidationError, match=problem):
        _settings(**overrides)


def test_the_rag_boundary_is_composed_from_settings() -> None:
    settings = _settings(
        vector_store=VectorStoreKind.MEMORY, embedding_provider=EmbeddingProviderKind.FAKE
    )
    store = InMemoryStore()

    def unit_of_work() -> InMemoryUnitOfWork:
        return InMemoryUnitOfWork(store)

    assert isinstance(
        build_retrieval_service(settings, unit_of_work=unit_of_work), RetrievalService
    )
    assert isinstance(build_rag_service(settings, unit_of_work=unit_of_work), RagService)
    for strategy, name in (
        (RetrievalStrategy.SEMANTIC, "vector"),
        (RetrievalStrategy.KEYWORD, "keyword"),
        (RetrievalStrategy.HYBRID, "hybrid"),
    ):
        configured = _settings(
            vector_store=VectorStoreKind.MEMORY,
            embedding_provider=EmbeddingProviderKind.FAKE,
            retrieval_strategy=strategy,
        )
        assert build_configured_retriever(configured, unit_of_work=unit_of_work).name == name
