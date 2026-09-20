"""Chunking use case: turn a document's persisted pages into chunks (§14).

``DocumentChunker`` is a pure computation over the pages the extraction stage stored; the
processor persists the result. It exists as a class so the configuration and tokenizer are
fixed once at composition time and every run of a document is reproducible.
"""

from collections.abc import Sequence
from uuid import UUID

from doculens.domain.chunking import (
    ChunkingConfig,
    RegexTokenizer,
    Tokenizer,
    chunk_document,
)
from doculens.domain.documents import DocumentChunk, DocumentPage


class DocumentChunker:
    def __init__(self, config: ChunkingConfig, tokenizer: Tokenizer | None = None) -> None:
        self._config = config
        self._tokenizer: Tokenizer = tokenizer if tokenizer is not None else RegexTokenizer()

    @property
    def config(self) -> ChunkingConfig:
        return self._config

    @property
    def tokenizer(self) -> Tokenizer:
        return self._tokenizer

    def chunk(self, document_id: UUID, pages: Sequence[DocumentPage]) -> list[DocumentChunk]:
        return chunk_document(document_id, pages, self._config, self._tokenizer)

    def describe(self) -> dict[str, object]:
        """The configuration recorded on the document for staleness checks (§32)."""
        return {
            "chunk_size": self._config.chunk_size,
            "chunk_overlap": self._config.chunk_overlap,
            "min_chunk_size": self._config.min_chunk_size,
            "tokenizer": self._tokenizer.name,
        }
