"""Question answering end to end on real infrastructure: PostgreSQL, PyMuPDF, ChromaDB, the
fake embedding provider and the fake language model, through the composition root.

Covers: a persisted, cited answer; a follow-up in the same conversation with history; the
insufficient-evidence path for a user without documents; and that a generation failure leaves
the conversation untouched.
"""

import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import SecretStr

from doculens.application.chunking import DocumentChunker
from doculens.application.ingestion import (
    DocumentIntakeService,
    DocumentProcessor,
    ProcessingOutcome,
    UploadLimits,
)
from doculens.application.rag import RagQuery
from doculens.domain.answering import AnswerOutcome
from doculens.domain.chunking import ChunkingConfig
from doculens.domain.conversations import MessageRole
from doculens.domain.llm import LLMProviderUnavailableError
from doculens.domain.prompting import INSUFFICIENT_EVIDENCE_STATEMENT
from doculens.domain.users import User
from doculens.infrastructure.config import CoreSettings, EmbeddingProviderKind, VectorStoreKind
from doculens.infrastructure.pdf import ExtractionLimits, PyMuPdfExtractor
from doculens.infrastructure.persistence.database import Database
from doculens.infrastructure.rag import build_rag_service
from doculens.infrastructure.storage import FilesystemObjectStorage
from doculens.infrastructure.vectors import ChromaVectorStore
from doculens.testing.chroma import VectorStoreUnavailableForTestsError, provisioned_chroma_url
from doculens.testing.embeddings import BagOfWordsEmbeddingProvider
from doculens.testing.factories import Factories
from doculens.testing.llm import FakeLLMProvider
from doculens.testing.pdfs import pdf_with_pages

pytestmark = pytest.mark.integration

DIMENSIONS = 64
UPLOAD_LIMITS = UploadLimits(
    max_file_size_bytes=256 * 1024, max_pages_per_document=10, max_documents_per_user=20
)


@pytest.fixture(scope="session")
def chroma_url() -> Iterator[str]:
    try:
        with provisioned_chroma_url() as url:
            yield url
    except VectorStoreUnavailableForTestsError as exc:
        if os.environ.get("CI"):
            raise
        pytest.skip(str(exc))


@pytest.fixture
async def vectors(chroma_url: str) -> AsyncIterator[ChromaVectorStore]:
    store = ChromaVectorStore.from_url(chroma_url, collection=f"doculens-answer-{uuid4().hex[:12]}")
    await store.ensure_collection()
    try:
        yield store
    finally:
        await store.drop_collection()


def answering_settings(settings: CoreSettings) -> CoreSettings:
    return CoreSettings(
        _env_file=None,
        database_url=SecretStr(settings.database_url.get_secret_value()),
        embedding_provider=EmbeddingProviderKind.FAKE,
        vector_store=VectorStoreKind.MEMORY,
        retrieval_candidates=8,
        context_max_chunks=3,
    )


class Stack:
    def __init__(
        self, database: Database, settings: CoreSettings, root: Path, vectors: ChromaVectorStore
    ) -> None:
        self.database = database
        self.embeddings = BagOfWordsEmbeddingProvider(dimensions=DIMENSIONS)
        self.llm = FakeLLMProvider()
        storage = FilesystemObjectStorage(root)
        self.intake = DocumentIntakeService(
            unit_of_work=database.unit_of_work, storage=storage, limits=UPLOAD_LIMITS
        )
        self.processor = DocumentProcessor(
            unit_of_work=database.unit_of_work,
            storage=storage,
            extractor=PyMuPdfExtractor(
                ExtractionLimits(
                    timeout_seconds=60.0,
                    memory_limit_bytes=None,
                    max_characters_per_page=100_000,
                    max_total_characters=1_000_000,
                )
            ),
            chunker=DocumentChunker(
                ChunkingConfig(chunk_size=48, chunk_overlap=4, min_chunk_size=4)
            ),
            embeddings=self.embeddings,
            vectors=vectors,
            limits=UPLOAD_LIMITS,
        )
        self.rag = build_rag_service(
            answering_settings(settings),
            unit_of_work=database.unit_of_work,
            embeddings=self.embeddings,
            vectors=vectors,
            llm=self.llm,
        )

    async def user(self, email: str) -> User:
        user = Factories.user(email)
        async with self.database.unit_of_work() as uow:
            await uow.users.add(user)
            await uow.commit()
        return user

    async def ingest(self, owner: User, pages: list[str], filename: str) -> None:
        document = await self.intake.accept(
            owner.id,
            filename=filename,
            declared_mime_type="application/pdf",
            data=pdf_with_pages(pages),
        )
        report = await self.processor.process(document.id)
        assert report.outcome is ProcessingOutcome.PROCESSED


@pytest.fixture
async def stack(
    database: Database, settings: CoreSettings, tmp_path: Path, vectors: ChromaVectorStore
) -> Stack:
    return Stack(database, settings, tmp_path / "objects", vectors)


async def test_an_answer_is_grounded_cited_and_persisted_with_its_conversation(
    stack: Stack,
) -> None:
    alice = await stack.user("alice@example.com")
    await stack.ingest(
        alice,
        [
            "Leave policy. Employees receive twenty-five days of annual leave.",
            "Parental leave. Parental leave is sixteen weeks for every employee.",
        ],
        "handbook.pdf",
    )

    result = await stack.rag.answer(
        RagQuery(owner_id=alice.id, question="How long is parental leave?")
    )

    assert result.outcome is AnswerOutcome.ANSWERED
    assert result.answer.endswith("[1]")
    (citation,) = result.citations
    assert citation.page_number == 2
    assert "sixteen weeks" in citation.quoted_text
    async with stack.database.unit_of_work() as uow:
        conversation = await uow.conversations.get(alice.id, result.conversation_id)
        messages = await uow.messages.list_for_conversation(alice.id, result.conversation_id)
        citations = await uow.messages.list_citations_for_conversation(
            alice.id, result.conversation_id
        )
        chunks = await uow.document_content.list_chunks(alice.id, citation.document_id)
    assert conversation is not None
    assert conversation.title == "How long is parental leave?"
    assert [(m.role, m.content) for m in messages] == [
        (MessageRole.USER, "How long is parental leave?"),
        (MessageRole.ASSISTANT, result.answer),
    ]
    assert citations == {result.assistant_message_id: [citation]}
    assert citation.chunk_id in {c.id for c in chunks}
    assert citation.quoted_text == next(c.text for c in chunks if c.id == citation.chunk_id)
    assert result.retrieval.retriever == "hybrid"
    assert result.usage.generation.requests == 1
    assert result.timing.total_ms >= result.timing.persistence_ms


async def test_a_follow_up_carries_the_conversation_and_stays_in_it(stack: Stack) -> None:
    alice = await stack.user("alice@example.com")
    await stack.ingest(
        alice, ["Annual leave is twenty-five days.", "Parental leave is sixteen weeks."], "hr.pdf"
    )
    first = await stack.rag.answer(RagQuery(owner_id=alice.id, question="How much annual leave?"))
    stack.llm.responses = ["How long is parental leave?", "Sixteen weeks [1]."]

    second = await stack.rag.answer(
        RagQuery(owner_id=alice.id, question="And parental?"), conversation_id=first.conversation_id
    )

    assert second.conversation_id == first.conversation_id
    assert second.retrieval.rewritten
    assert second.retrieval.query == "How long is parental leave?"
    assert second.citations[0].page_number == 2
    async with stack.database.unit_of_work() as uow:
        messages = await uow.messages.list_for_conversation(alice.id, first.conversation_id)
    assert [m.content for m in messages] == [
        "How much annual leave?",
        first.answer,
        "And parental?",
        "Sixteen weeks [1].",
    ]


async def test_without_documents_the_answer_states_insufficient_evidence(stack: Stack) -> None:
    mallory = await stack.user("mallory@example.com")

    result = await stack.rag.answer(
        RagQuery(owner_id=mallory.id, question="What is the leave policy?")
    )

    assert result.outcome is AnswerOutcome.INSUFFICIENT_EVIDENCE
    assert result.answer == INSUFFICIENT_EVIDENCE_STATEMENT
    assert result.citations == ()
    assert stack.llm.calls == []
    async with stack.database.unit_of_work() as uow:
        messages = await uow.messages.list_for_conversation(mallory.id, result.conversation_id)
    assert [m.role for m in messages] == [MessageRole.USER, MessageRole.ASSISTANT]


async def test_a_generation_failure_leaves_no_trace(stack: Stack) -> None:
    alice = await stack.user("alice@example.com")
    await stack.ingest(alice, ["Annual leave is twenty-five days."], "hr.pdf")
    stack.llm.failures = [LLMProviderUnavailableError(provider="fake")]

    with pytest.raises(LLMProviderUnavailableError):
        await stack.rag.answer(RagQuery(owner_id=alice.id, question="How much annual leave?"))

    async with stack.database.unit_of_work() as uow:
        conversations = await uow.conversations.list_for_owner(alice.id)
    assert conversations == []
