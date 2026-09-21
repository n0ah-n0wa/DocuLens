"""Persistent conversations over HTTP (SPECIFICATIONS.md §9, §21, §23, §28, §33): create, list,
inspect, rename, delete, and continue a conversation by asking questions that are answered from
the caller's own documents and persisted with their citations."""

import asyncio
from dataclasses import replace
from http import HTTPStatus
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from doculens.application.vectors import records_for_chunks
from doculens.domain.chunking import chunk_id
from doculens.domain.documents import DocumentChunk, ProcessingStatus
from doculens.domain.prompting import INSUFFICIENT_EVIDENCE_STATEMENT, SYSTEM_INSTRUCTIONS
from doculens.testing.embeddings import BagOfWordsEmbeddingProvider
from doculens.testing.factories import Factories
from doculens.testing.fakes import InMemoryStore, InMemoryUnitOfWork
from doculens.testing.llm import FakeLLMProvider
from doculens_api.main import create_app
from doculens_api.settings import ApiSettings

if TYPE_CHECKING:
    from doculens_api.dependencies import AppComponents

pytestmark = pytest.mark.api

PASSWORD = "correct horse battery staple"  # noqa: S105 - test-only value
TEXTS = [
    "Annual leave is twenty-five days per year.",
    "Parental leave is sixteen weeks at full pay.",
    "Remote work is allowed three days per week.",
]


@pytest.fixture
def llm() -> FakeLLMProvider:
    return FakeLLMProvider()


@pytest.fixture
def embeddings(settings: ApiSettings) -> BagOfWordsEmbeddingProvider:
    return BagOfWordsEmbeddingProvider(dimensions=64, model_name=settings.embedding_model)


@pytest.fixture
def app(
    settings: ApiSettings,
    store: InMemoryStore,
    llm: FakeLLMProvider,
    embeddings: BagOfWordsEmbeddingProvider,
) -> FastAPI:
    return create_app(
        settings,
        probes=[],
        unit_of_work_factory=lambda: InMemoryUnitOfWork(store),
        llm_provider=llm,
        embedding_provider=embeddings,
    )


def _login(client: TestClient, email: str) -> dict[str, str]:
    client.post("/api/v1/auth/register", json={"email": email, "password": PASSWORD})
    response = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == HTTPStatus.OK, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _index(
    app: FastAPI,
    store: InMemoryStore,
    embeddings: BagOfWordsEmbeddingProvider,
    owner_id: UUID,
    texts: list[str],
) -> UUID:
    """A READY document with one chunk per text: rows in the store, vectors in the app's vector
    store, embedded by the same provider the app embeds queries with."""
    components: AppComponents = app.state.components
    document = replace(
        Factories.document(owner_id),
        processing_status=ProcessingStatus.READY,
        chunk_count=len(texts),
        filename="handbook.pdf",
        content_hash=uuid4().hex * 2,
    )
    store.documents[document.id] = document
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
        store.chunks[chunk.id] = chunk
    embedded = asyncio.run(embeddings.embed_documents(texts))
    asyncio.run(
        components.vector_store.upsert(
            owner_id,
            records_for_chunks(
                chunks, embedded, owner_id=owner_id, collection_id=None, embedding_provider="fake"
            ),
        )
    )
    return document.id


def _conversation(client: TestClient, headers: dict[str, str], title: str = "x") -> str:
    created = client.post("/api/v1/conversations", json={"title": title}, headers=headers)
    assert created.status_code == HTTPStatus.CREATED, created.text
    return str(created.json()["id"])


def _user_id(store: InMemoryStore, email: str) -> UUID:
    return next(u.id for u in store.users.values() if u.email == email)


def test_conversations_are_created_listed_inspected_renamed_and_deleted(
    client: TestClient,
) -> None:
    alice = _login(client, "alice@example.com")

    first = client.post("/api/v1/conversations", json={"title": "  Risks  "}, headers=alice)
    assert first.status_code == HTTPStatus.CREATED, first.text
    assert first.json()["title"] == "Risks"
    assert first.json()["collection_id"] is None
    second = client.post("/api/v1/conversations", json={"title": "Costs"}, headers=alice)
    first_id, second_id = first.json()["id"], second.json()["id"]

    listed = client.get("/api/v1/conversations", headers=alice)
    assert listed.status_code == HTTPStatus.OK
    assert [c["id"] for c in listed.json()] == [second_id, first_id]  # most recent first

    inspected = client.get(f"/api/v1/conversations/{first_id}", headers=alice)
    assert inspected.status_code == HTTPStatus.OK
    assert inspected.json() == first.json()

    renamed = client.patch(
        f"/api/v1/conversations/{first_id}", json={"title": "Main risks"}, headers=alice
    )
    assert renamed.status_code == HTTPStatus.OK
    assert renamed.json()["title"] == "Main risks"
    assert renamed.json()["updated_at"] >= first.json()["updated_at"]
    assert [c["id"] for c in client.get("/api/v1/conversations", headers=alice).json()] == [
        first_id,
        second_id,
    ]

    blank = client.patch(f"/api/v1/conversations/{first_id}", json={"title": "  "}, headers=alice)
    assert blank.status_code in (HTTPStatus.BAD_REQUEST, HTTPStatus.UNPROCESSABLE_ENTITY)

    deleted = client.delete(f"/api/v1/conversations/{first_id}", headers=alice)
    assert deleted.status_code == HTTPStatus.NO_CONTENT
    assert client.get(f"/api/v1/conversations/{first_id}", headers=alice).status_code == (
        HTTPStatus.NOT_FOUND
    )
    assert client.delete(f"/api/v1/conversations/{first_id}", headers=alice).status_code == (
        HTTPStatus.NOT_FOUND
    )
    assert [c["id"] for c in client.get("/api/v1/conversations", headers=alice).json()] == [
        second_id
    ]


def test_a_conversation_can_be_scoped_to_a_collection_the_caller_owns(client: TestClient) -> None:
    alice = _login(client, "alice@example.com")
    bob = _login(client, "bob@example.com")
    mine = client.post("/api/v1/collections", json={"name": "HR"}, headers=alice).json()["id"]
    theirs = client.post("/api/v1/collections", json={"name": "Bob"}, headers=bob).json()["id"]

    scoped = client.post(
        "/api/v1/conversations", json={"title": "HR", "collection_id": mine}, headers=alice
    )
    assert scoped.status_code == HTTPStatus.CREATED
    assert scoped.json()["collection_id"] == mine

    foreign = client.post(
        "/api/v1/conversations", json={"title": "x", "collection_id": theirs}, headers=alice
    )
    assert foreign.status_code == HTTPStatus.NOT_FOUND
    assert foreign.json()["error"]["code"] == "COLLECTION_NOT_FOUND"

    detached = client.patch(
        f"/api/v1/conversations/{scoped.json()['id']}", json={"collection_id": None}, headers=alice
    )
    assert detached.status_code == HTTPStatus.OK
    assert detached.json()["collection_id"] is None


def test_asking_persists_the_exchange_with_citations(
    client: TestClient,
    app: FastAPI,
    store: InMemoryStore,
    llm: FakeLLMProvider,
    embeddings: BagOfWordsEmbeddingProvider,
) -> None:
    alice = _login(client, "alice@example.com")
    document_id = _index(app, store, embeddings, _user_id(store, "alice@example.com"), TEXTS)
    conversation = client.post("/api/v1/conversations", json={"title": "HR"}, headers=alice).json()

    asked = client.post(
        f"/api/v1/conversations/{conversation['id']}/messages",
        json={"question": "How long is parental leave?"},
        headers=alice,
    )

    assert asked.status_code == HTTPStatus.CREATED, asked.text
    body = asked.json()
    assert body["conversation_id"] == conversation["id"]
    assert body["outcome"] == "answered"
    assert body["user_message"]["role"] == "USER"
    assert body["user_message"]["content"] == "How long is parental leave?"
    assert body["user_message"]["citations"] == []
    assistant = body["assistant_message"]
    assert assistant["role"] == "ASSISTANT"
    assert assistant["content"].endswith("[1]")
    (citation,) = assistant["citations"]
    assert citation["document_id"] == str(document_id)
    assert citation["page_number"] == 2
    assert "sixteen weeks" in citation["quoted_text"]
    assert citation["chunk_id"] is not None
    assert body["retrieval"]["retriever"] == "hybrid"
    assert body["retrieval"]["documents_in_scope"] == 1
    assert body["retrieval"]["model"] == "fake-llm-v1"
    assert body["retrieval"]["prompt_version"] == "grounded-v1"
    assert body["retrieval"]["uncited"] is False
    assert body["usage"]["llm_requests"] == 1
    assert body["usage"]["embedding_requests"] == 1
    assert body["timing"]["total_ms"] >= 0
    # The model saw the grounded prompt: the policy first, the question last.
    (call,) = llm.calls
    assert call[0].content == SYSTEM_INSTRUCTIONS
    assert call[-1].content.endswith("<question>\nHow long is parental leave?\n</question>")

    history = client.get(f"/api/v1/conversations/{conversation['id']}/messages", headers=alice)
    assert history.status_code == HTTPStatus.OK
    assert [m["role"] for m in history.json()] == ["USER", "ASSISTANT"]
    assert history.json()[1] == assistant
    assert history.json()[0]["id"] == body["user_message"]["id"]
    listed = client.get("/api/v1/conversations", headers=alice).json()
    assert listed[0]["updated_at"] >= conversation["updated_at"]


def test_follow_up_questions_continue_the_conversation(
    client: TestClient,
    app: FastAPI,
    store: InMemoryStore,
    llm: FakeLLMProvider,
    embeddings: BagOfWordsEmbeddingProvider,
) -> None:
    alice = _login(client, "alice@example.com")
    _index(app, store, embeddings, _user_id(store, "alice@example.com"), TEXTS)
    conversation_id = client.post(
        "/api/v1/conversations", json={"title": "HR"}, headers=alice
    ).json()["id"]
    url = f"/api/v1/conversations/{conversation_id}/messages"
    first = client.post(url, json={"question": "How much annual leave do I get?"}, headers=alice)
    assert first.status_code == HTTPStatus.CREATED
    llm.responses = ["How long is parental leave?", "Sixteen weeks [1]."]

    second = client.post(url, json={"question": "And for parents?"}, headers=alice)

    assert second.status_code == HTTPStatus.CREATED, second.text
    body = second.json()
    assert body["retrieval"]["rewritten"] is True
    assert body["retrieval"]["query"] == "How long is parental leave?"
    assert body["user_message"]["content"] == "And for parents?"
    assert body["assistant_message"]["content"] == "Sixteen weeks [1]."
    assert body["assistant_message"]["citations"][0]["page_number"] == 2
    assert body["usage"]["llm_requests"] == 2  # the rewrite and the answer
    history = client.get(url, headers=alice).json()
    assert [m["content"] for m in history] == [
        "How much annual leave do I get?",
        first.json()["assistant_message"]["content"],
        "And for parents?",
        "Sixteen weeks [1].",
    ]
    answer_call = llm.calls[-1]
    assert [m.role.value for m in answer_call] == ["SYSTEM", "USER", "ASSISTANT", "USER"]


def test_without_documents_the_answer_states_insufficient_evidence(
    client: TestClient, llm: FakeLLMProvider
) -> None:
    alice = _login(client, "alice@example.com")
    conversation_id = client.post(
        "/api/v1/conversations", json={"title": "x"}, headers=alice
    ).json()["id"]

    asked = client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        json={"question": "What is the leave policy?"},
        headers=alice,
    )

    assert asked.status_code == HTTPStatus.CREATED
    assert asked.json()["outcome"] == "insufficient_evidence"
    assert asked.json()["assistant_message"]["content"] == INSUFFICIENT_EVIDENCE_STATEMENT
    assert asked.json()["assistant_message"]["citations"] == []
    assert asked.json()["usage"]["llm_requests"] == 0
    assert llm.calls == []


def test_a_question_can_be_scoped_to_selected_documents(
    client: TestClient, app: FastAPI, store: InMemoryStore, embeddings: BagOfWordsEmbeddingProvider
) -> None:
    alice = _login(client, "alice@example.com")
    alice_id = _user_id(store, "alice@example.com")
    hr = _index(app, store, embeddings, alice_id, TEXTS)
    cookbook = _index(app, store, embeddings, alice_id, ["Proof the dough for twelve hours."])
    conversation_id = client.post(
        "/api/v1/conversations", json={"title": "x"}, headers=alice
    ).json()["id"]
    url = f"/api/v1/conversations/{conversation_id}/messages"

    scoped = client.post(
        url, json={"question": "How long to proof?", "document_ids": [str(cookbook)]}, headers=alice
    )
    assert scoped.status_code == HTTPStatus.CREATED
    assert scoped.json()["retrieval"]["documents_in_scope"] == 1
    assert {c["document_id"] for c in scoped.json()["assistant_message"]["citations"]} == {
        str(cookbook)
    }

    unscoped = client.post(url, json={"question": "How long to proof?"}, headers=alice)
    assert unscoped.json()["retrieval"]["documents_in_scope"] == 2
    assert hr != cookbook

    foreign = client.post(
        url, json={"question": "x", "document_ids": [str(uuid4())]}, headers=alice
    )
    assert foreign.status_code == HTTPStatus.NOT_FOUND
    assert foreign.json()["error"]["code"] == "DOCUMENT_NOT_FOUND"


def test_asking_in_another_users_conversation_is_not_found_and_records_nothing(
    client: TestClient, store: InMemoryStore, llm: FakeLLMProvider
) -> None:
    alice = _login(client, "alice@example.com")
    bob = _login(client, "bob@example.com")
    conversation_id = client.post(
        "/api/v1/conversations", json={"title": "x"}, headers=alice
    ).json()["id"]

    for headers, target in ((bob, conversation_id), (alice, uuid4())):
        asked = client.post(
            f"/api/v1/conversations/{target}/messages",
            json={"question": "What are the risks?"},
            headers=headers,
        )
        assert asked.status_code == HTTPStatus.NOT_FOUND
        assert asked.json()["error"]["code"] == "CONVERSATION_NOT_FOUND"

    assert store.messages == {}
    assert llm.calls == []
    assert client.get(
        f"/api/v1/conversations/{conversation_id}/messages", headers=bob
    ).status_code == (HTTPStatus.NOT_FOUND)
    assert client.get("/api/v1/conversations", headers=bob).json() == []


def test_invalid_questions_are_rejected_before_any_work(
    client: TestClient, store: InMemoryStore, llm: FakeLLMProvider
) -> None:
    alice = _login(client, "alice@example.com")
    conversation_id = client.post(
        "/api/v1/conversations", json={"title": "x"}, headers=alice
    ).json()["id"]
    url = f"/api/v1/conversations/{conversation_id}/messages"

    assert client.post(url, json={"question": ""}, headers=alice).status_code == (
        HTTPStatus.UNPROCESSABLE_ENTITY
    )
    assert client.post(url, json={}, headers=alice).status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    too_long = client.post(url, json={"question": "x" * 5_000}, headers=alice)
    assert too_long.status_code == HTTPStatus.BAD_REQUEST
    assert too_long.json()["error"]["code"] == "INVALID_QUERY"
    empty_scope = client.post(url, json={"question": "q", "document_ids": []}, headers=alice)
    assert empty_scope.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert client.post(url, json={"question": "q"}).status_code == HTTPStatus.UNAUTHORIZED
    assert store.messages == {}
    assert llm.calls == []


def test_deleting_a_conversation_removes_its_messages_and_citations(
    client: TestClient, store: InMemoryStore
) -> None:
    alice = _login(client, "alice@example.com")
    conversation_id = client.post(
        "/api/v1/conversations", json={"title": "x"}, headers=alice
    ).json()["id"]
    client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        json={"question": "Anything?"},
        headers=alice,
    )
    assert len(store.messages) == 2

    assert client.delete(f"/api/v1/conversations/{conversation_id}", headers=alice).status_code == (
        HTTPStatus.NO_CONTENT
    )

    assert store.messages == {}
    assert store.citations == {}
    assert UUID(conversation_id) not in store.conversations
