"""Keyword retrieval through PostgreSQL full-text search (§18, OQ-4): matching, ranking, the
scope filters, owner isolation and empty results, all through the repository port."""

from dataclasses import replace
from uuid import UUID

import pytest

from doculens.domain.documents import Document, DocumentChunk, DocumentPage, ProcessingStatus
from doculens.domain.ids import new_id
from doculens.domain.retrieval import ChunkMatch
from doculens.domain.vectors import SearchFilter
from doculens.infrastructure.persistence.database import Database
from doculens.testing.factories import Factories

pytestmark = pytest.mark.integration


class Seeded:
    def __init__(self, database: Database) -> None:
        self.database = database
        self.owner = Factories.user("owner@example.com")
        self.stranger = Factories.user("stranger@example.com")
        self.collection = Factories.collection(self.owner.id)

    async def setup(self) -> None:
        async with self.database.unit_of_work() as uow:
            await uow.users.add(self.owner)
            await uow.users.add(self.stranger)
            await uow.collections.add(self.collection)
            await uow.commit()

    async def document(
        self, texts: list[str], *, owner: UUID | None = None, collection: UUID | None = None
    ) -> Document:
        owner = owner or self.owner.id
        document = replace(
            Factories.document(owner, collection),
            content_hash=new_id().hex * 2,
            processing_status=ProcessingStatus.READY,
        )
        page = DocumentPage(
            id=new_id(),
            document_id=document.id,
            page_number=1,
            extracted_text=" ".join(texts),
            character_count=sum(len(t) for t in texts),
            metadata={},
        )
        chunks = [
            DocumentChunk(
                id=new_id(),
                document_id=document.id,
                page_id=page.id,
                chunk_index=index,
                text=text,
                token_count=len(text.split()),
                metadata={"page_number": 1},
            )
            for index, text in enumerate(texts)
        ]
        async with self.database.unit_of_work() as uow:
            await uow.documents.add(document)
            await uow.document_content.add_pages([page])
            await uow.document_content.add_chunks(chunks)
            await uow.commit()
        return document

    async def search(
        self, query: str, *, owner: UUID | None = None, limit: int = 10, **scope: object
    ) -> list[ChunkMatch]:
        async with self.database.unit_of_work() as uow:
            return await uow.document_content.search_chunks(
                query,
                scope=SearchFilter(owner_id=owner or self.owner.id, **scope),  # type: ignore[arg-type]
                limit=limit,
            )


@pytest.fixture
async def seeded(database: Database) -> Seeded:
    world = Seeded(database)
    await world.setup()
    return world


async def test_any_term_matches_and_more_terms_rank_higher(seeded: Seeded) -> None:
    await seeded.document(
        ["alpha only here", "alpha and beta here", "alpha, beta and gamma here", "delta"]
    )

    matches = await seeded.search("Alpha BETA gamma")

    assert [m.chunk.chunk_index for m in matches] == [2, 1, 0]
    assert matches[0].score > matches[1].score > matches[2].score
    assert [int(m.score) for m in matches] == [3, 2, 1]  # distinct terms matched
    assert matches[0].chunk.text == "alpha, beta and gamma here"


async def test_distinct_terms_beat_a_repeated_common_word(seeded: Seeded) -> None:
    await seeded.document(
        [
            "the the the the the the the",  # one term, many times
            "revenue in the quarter",  # three distinct terms, once each
            "the revenue the revenue",  # two distinct terms, repeated
        ]
    )

    matches = await seeded.search("how did revenue grow in the quarter")

    assert [m.chunk.chunk_index for m in matches] == [1, 2, 0]
    assert matches[0].score > 3 > matches[1].score > 2 > matches[2].score > 1


async def test_terms_are_matched_as_whole_words_without_stemming(seeded: Seeded) -> None:
    await seeded.document(["tokens rotate", "token rotation"])

    assert [m.chunk.chunk_index for m in await seeded.search("tokens")] == [0]
    assert [m.chunk.chunk_index for m in await seeded.search("rotation")] == [1]


async def test_punctuation_and_query_syntax_in_the_question_are_harmless(seeded: Seeded) -> None:
    await seeded.document(["the invoice INV-2291 is due", "nothing relevant"])

    matches = await seeded.search("invoice (INV-2291)! & | :* <-> 'quoted'")

    assert [m.chunk.chunk_index for m in matches] == [0]
    assert await seeded.search("?!?") == []
    assert await seeded.search("   ") == []


async def test_unicode_text_is_searchable(seeded: Seeded) -> None:
    await seeded.document(["Die Straße ist naß", "日本語のテキスト", "plain ascii"])

    assert [m.chunk.chunk_index for m in await seeded.search("straße")] == [0]
    assert [m.chunk.chunk_index for m in await seeded.search("日本語のテキスト")] == [1]


async def test_scope_filters_restrict_the_matches(seeded: Seeded) -> None:
    in_collection = await seeded.document(["the shared phrase"], collection=seeded.collection.id)
    selected = await seeded.document(["the shared phrase"])
    other = await seeded.document(["the shared phrase"])

    everything = await seeded.search("shared phrase")
    by_collection = await seeded.search("shared phrase", collection_id=seeded.collection.id)
    by_selection = await seeded.search("shared phrase", document_ids=(selected.id, other.id))

    assert {m.chunk.document_id for m in everything} == {in_collection.id, selected.id, other.id}
    assert [m.chunk.document_id for m in by_collection] == [in_collection.id]
    assert {m.chunk.document_id for m in by_selection} == {selected.id, other.id}
    assert await seeded.search("shared phrase", document_ids=(new_id(),)) == []


async def test_another_owners_chunks_are_never_matched(seeded: Seeded) -> None:
    theirs = await seeded.document(["the confidential merger plan"], owner=seeded.stranger.id)
    mine = await seeded.document(["my own notes"])

    assert [m.chunk.document_id for m in await seeded.search("confidential merger plan")] == []
    assert [m.chunk.document_id for m in await seeded.search("notes")] == [mine.id]
    # Even naming their document explicitly yields nothing.
    assert await seeded.search("confidential", document_ids=(theirs.id,)) == []
    stranger_view = await seeded.search("confidential merger", owner=seeded.stranger.id)
    assert [m.chunk.document_id for m in stranger_view] == [theirs.id]


async def test_no_match_and_limit(seeded: Seeded) -> None:
    await seeded.document([f"passage {i} about the budget" for i in range(6)])

    assert await seeded.search("unicorns") == []
    assert await seeded.search("budget", limit=0) == []
    limited = await seeded.search("budget", limit=4)
    assert len(limited) == 4
    assert [m.chunk.id for m in limited] == sorted(m.chunk.id for m in limited)  # tie order
    assert limited == await seeded.search("budget", limit=4)
