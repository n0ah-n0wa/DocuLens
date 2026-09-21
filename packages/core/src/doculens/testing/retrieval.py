"""A small indexed corpus for retrieval tests: two users, chunk rows in the in-memory store and
vectors in whichever ``VectorStore`` the test supplies (in-memory or a live ChromaDB)."""

from dataclasses import replace
from uuid import UUID, uuid4

from doculens.application.vectors import VectorStore, records_for_chunks
from doculens.domain.chunking import chunk_id
from doculens.domain.documents import Document, DocumentChunk, ProcessingStatus
from doculens.testing.embeddings import BagOfWordsEmbeddingProvider, FakeEmbeddingProvider
from doculens.testing.factories import Factories
from doculens.testing.fakes import InMemoryStore, InMemoryUnitOfWork


class IndexedCorpus[VectorStoreT: VectorStore]:
    def __init__(
        self,
        vectors: VectorStoreT,
        *,
        embeddings: FakeEmbeddingProvider | None = None,
        dimensions: int = 32,
    ) -> None:
        self.store = InMemoryStore()
        self.vectors: VectorStoreT = vectors
        self.embeddings = embeddings or BagOfWordsEmbeddingProvider(dimensions=dimensions)
        self.owner = Factories.user("owner@example.com")
        self.stranger = Factories.user("stranger@example.com")
        self.store.users[self.owner.id] = self.owner
        self.store.users[self.stranger.id] = self.stranger
        self.collection = Factories.collection(self.owner.id, "Architecture")
        self.store.collections[self.collection.id] = self.collection

    def unit_of_work(self) -> InMemoryUnitOfWork:
        return InMemoryUnitOfWork(self.store)

    async def index(
        self,
        texts: list[str],
        *,
        owner: UUID | None = None,
        collection: UUID | None = None,
        status: ProcessingStatus = ProcessingStatus.READY,
        filename: str = "report.pdf",
    ) -> Document:
        """A READY document with one chunk per text (page n = chunk n), rows and vectors."""
        owner = owner or self.owner.id
        document = replace(
            Factories.document(owner, collection),
            processing_status=status,
            filename=filename,
            content_hash=uuid4().hex * 2,
            chunk_count=len(texts),
        )
        self.store.documents[document.id] = document
        chunks = [
            DocumentChunk(
                id=chunk_id(document.id, index),
                document_id=document.id,
                page_id=uuid4(),
                chunk_index=index,
                text=text,
                token_count=len(text.split()),
                metadata={"page_number": index + 1, "content_hash": f"h:{text}"},
                vector_id=str(chunk_id(document.id, index)),
            )
            for index, text in enumerate(texts)
        ]
        for chunk in chunks:
            self.store.chunks[chunk.id] = chunk
        embedded = await self.embeddings.embed_documents(texts)
        await self.vectors.upsert(
            owner,
            records_for_chunks(
                chunks,
                embedded,
                owner_id=owner,
                collection_id=collection,
                embedding_provider=self.embeddings.name,
            ),
        )
        return document


__all__ = ["IndexedCorpus"]
