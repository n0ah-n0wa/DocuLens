"""Vector store adapters and their composition from settings (§16, §18)."""

from doculens.application.vectors import VectorStore
from doculens.domain.vectors import collection_name
from doculens.infrastructure.config import CoreSettings, VectorStoreKind
from doculens.infrastructure.vectors.chroma import ChromaVectorStore, VectorStoreProbe
from doculens.testing.vectors import InMemoryVectorStore


def build_vector_store(settings: CoreSettings) -> VectorStore:
    """The store selected by ``VECTOR_STORE``, indexing the configured embedding model."""
    name = collection_name(settings.chroma_collection_prefix, settings.embedding_model)
    if settings.vector_store is VectorStoreKind.MEMORY:
        return InMemoryVectorStore(name, max_results=settings.vector_search_max_results)
    token = settings.chroma_api_token
    return ChromaVectorStore.from_url(
        settings.chroma_url,
        collection=name,
        api_token=token.get_secret_value() if token is not None else None,
        timeout_seconds=settings.chroma_timeout_seconds,
        collection_metadata={
            "embedding_model": settings.embedding_model,
            "embedding_provider": settings.embedding_provider.value,
        },
        max_results=settings.vector_search_max_results,
    )


__all__ = ["ChromaVectorStore", "VectorStoreProbe", "build_vector_store"]
