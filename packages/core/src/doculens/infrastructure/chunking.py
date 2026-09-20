"""Composition of the chunker from settings."""

from doculens.application.chunking import DocumentChunker
from doculens.domain.chunking import ChunkingConfig, RegexTokenizer
from doculens.infrastructure.config import CoreSettings


def build_chunking_config(settings: CoreSettings) -> ChunkingConfig:
    return ChunkingConfig(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
        min_chunk_size=settings.min_chunk_size,
    )


def build_chunker(settings: CoreSettings) -> DocumentChunker:
    """The provisional tokenizer (OQ-16); the embedding provider's tokenizer replaces it later."""
    return DocumentChunker(build_chunking_config(settings), RegexTokenizer())
