"""The in-memory vector store honours the shared contract (the same suite ChromaDB runs)."""

import pytest

from doculens.testing.vector_contract import VectorStoreContract
from doculens.testing.vectors import InMemoryVectorStore

pytestmark = pytest.mark.unit


@pytest.fixture
def store() -> InMemoryVectorStore:
    return InMemoryVectorStore()


class TestInMemoryVectorStore(VectorStoreContract):
    pass
