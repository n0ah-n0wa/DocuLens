"""Composition of the RAG boundary from settings (§17 to §20, §73)."""

from doculens.application.answering import (
    AnswerLimits,
    AnswerService,
    LLMQueryRewriter,
    NoQueryRewriting,
    QueryRewriter,
)
from doculens.application.embeddings import EmbeddingProvider
from doculens.application.llm import LLMProvider
from doculens.application.rag import RagService
from doculens.application.retrieval import RetrievalService, Retriever, build_retriever
from doculens.application.unit_of_work import UnitOfWorkFactory
from doculens.application.vectors import VectorStore
from doculens.domain.llm import GenerationOptions
from doculens.domain.prompting import PromptBuilder, PromptLimits
from doculens.domain.retrieval import ContextLimits, RetrievalLimits
from doculens.infrastructure.config import CoreSettings
from doculens.infrastructure.embeddings import build_embedding_provider
from doculens.infrastructure.llm import build_llm_provider
from doculens.infrastructure.reranking import build_reranking_stage
from doculens.infrastructure.vectors import build_vector_store


def build_retrieval_limits(settings: CoreSettings) -> RetrievalLimits:
    return RetrievalLimits(
        max_query_characters=settings.retrieval_max_query_characters,
        candidate_limit=settings.retrieval_candidates,
        min_score=settings.retrieval_min_score,
        max_scope_documents=settings.retrieval_max_scope_documents,
        timeout_seconds=settings.retrieval_timeout_seconds,
    )


def build_context_limits(settings: CoreSettings) -> ContextLimits:
    return ContextLimits(
        max_chunks=settings.context_max_chunks,
        max_characters=settings.context_max_characters,
        max_chunks_per_document=settings.context_max_chunks_per_document,
    )


def build_prompt_limits(settings: CoreSettings) -> PromptLimits:
    return PromptLimits(
        max_history_messages=settings.prompt_max_history_messages,
        max_history_characters=settings.prompt_max_history_characters,
    )


def build_prompt_builder(settings: CoreSettings) -> PromptBuilder:
    return PromptBuilder(build_prompt_limits(settings))


def build_configured_retriever(
    settings: CoreSettings,
    *,
    unit_of_work: UnitOfWorkFactory,
    embeddings: EmbeddingProvider | None = None,
    vectors: VectorStore | None = None,
) -> Retriever:
    """The retriever ``RETRIEVAL_STRATEGY`` selects, with adapters built from settings."""
    return build_retriever(
        settings.retrieval_strategy,
        unit_of_work=unit_of_work,
        embeddings=embeddings or build_embedding_provider(settings),
        vectors=vectors or build_vector_store(settings),
        min_score=settings.retrieval_min_score,
    )


def build_retrieval_service(
    settings: CoreSettings,
    *,
    unit_of_work: UnitOfWorkFactory,
    embeddings: EmbeddingProvider | None = None,
    vectors: VectorStore | None = None,
) -> RetrievalService:
    return RetrievalService(
        unit_of_work=unit_of_work,
        retriever=build_configured_retriever(
            settings, unit_of_work=unit_of_work, embeddings=embeddings, vectors=vectors
        ),
        limits=build_retrieval_limits(settings),
        context_limits=build_context_limits(settings),
        reranking=build_reranking_stage(settings),
    )


def build_query_rewriter(settings: CoreSettings, llm: LLMProvider) -> QueryRewriter:
    if not settings.query_rewriting:
        return NoQueryRewriting()
    return LLMQueryRewriter(
        llm,
        max_characters=settings.retrieval_max_query_characters,
        timeout_seconds=settings.query_rewrite_timeout_seconds,
    )


def build_answer_service(
    settings: CoreSettings,
    *,
    unit_of_work: UnitOfWorkFactory,
    retrieval: RetrievalService | None = None,
    llm: LLMProvider | None = None,
    embeddings: EmbeddingProvider | None = None,
    vectors: VectorStore | None = None,
) -> AnswerService:
    model = llm or build_llm_provider(settings)
    return AnswerService(
        unit_of_work=unit_of_work,
        retrieval=retrieval
        or build_retrieval_service(
            settings, unit_of_work=unit_of_work, embeddings=embeddings, vectors=vectors
        ),
        prompt_builder=build_prompt_builder(settings),
        llm=model,
        rewriter=build_query_rewriter(settings, model),
        options=GenerationOptions(
            max_output_tokens=settings.llm_max_output_tokens,
            temperature=settings.llm_temperature,
        ),
        limits=AnswerLimits(
            max_quote_characters=settings.citation_max_quote_characters,
            max_history_messages=settings.prompt_max_history_messages,
            generation_timeout_seconds=settings.generation_timeout_seconds,
        ),
    )


def build_rag_service(
    settings: CoreSettings,
    *,
    unit_of_work: UnitOfWorkFactory,
    embeddings: EmbeddingProvider | None = None,
    vectors: VectorStore | None = None,
    llm: LLMProvider | None = None,
) -> RagService:
    retrieval = build_retrieval_service(
        settings, unit_of_work=unit_of_work, embeddings=embeddings, vectors=vectors
    )
    return RagService(
        retrieval=retrieval,
        answering=build_answer_service(
            settings, unit_of_work=unit_of_work, retrieval=retrieval, llm=llm
        ),
    )


__all__ = [
    "build_answer_service",
    "build_configured_retriever",
    "build_context_limits",
    "build_prompt_builder",
    "build_prompt_limits",
    "build_query_rewriter",
    "build_rag_service",
    "build_retrieval_limits",
    "build_retrieval_service",
]
