"""Pure retrieval rules: query preprocessing, scope, candidate selection, context assembly."""

from dataclasses import replace
from uuid import UUID, uuid4

import pytest

from doculens.domain.chunking import chunk_id
from doculens.domain.documents import Document, DocumentChunk, ProcessingStatus
from doculens.domain.retrieval import (
    MAX_KEYWORD_TERMS,
    ContextLimits,
    Evidence,
    InvalidQueryError,
    InvalidRetrievalScopeError,
    RetrievalLimits,
    RetrievalScope,
    assemble_context,
    fuse_rankings,
    keyword_terms,
    prepare_query,
    resolve_scope,
    select_candidates,
)
from doculens.domain.vectors import SearchHit, VectorMetadata, vector_id_for
from doculens.testing.factories import Factories

pytestmark = pytest.mark.unit

OWNER = uuid4()


# -- query preprocessing --------------------------------------------------------------------------


def test_a_question_is_normalised_deterministically() -> None:
    # Ligatures, an em space and full-width digits, escaped so the intent stays visible.
    raw = "  What\tis the\r\n\x00 \ufb01nal  \ufb01gure\u2003for \uff12\uff10\uff12\uff14?  "

    prepared = prepare_query(raw, max_characters=200)

    assert prepared.original == raw
    assert prepared.text == "What is the final figure for 2024?"
    assert prepare_query(raw, max_characters=200) == prepared


def test_case_is_preserved_and_lone_surrogates_are_replaced() -> None:
    prepared = prepare_query("Compare RFC 7519 and \ud800 JWT", max_characters=100)

    assert prepared.text == "Compare RFC 7519 and � JWT"


@pytest.mark.parametrize("question", ["", "   ", "\x00\x01\x1f", "\n\t"])
def test_an_empty_question_is_refused(question: str) -> None:
    with pytest.raises(InvalidQueryError, match="empty"):
        prepare_query(question, max_characters=100)


def test_an_over_long_question_is_refused_before_any_provider_call() -> None:
    with pytest.raises(InvalidQueryError, match="exceeds 10"):
        prepare_query("x" * 11, max_characters=10)
    assert prepare_query("x" * 10, max_characters=10).text == "x" * 10


def test_limits_are_validated() -> None:
    with pytest.raises(ValueError, match="positive"):
        RetrievalLimits(candidate_limit=0)
    with pytest.raises(ValueError, match="cosine"):
        RetrievalLimits(min_score=1.5)
    with pytest.raises(ValueError, match="timeout"):
        RetrievalLimits(timeout_seconds=0)
    with pytest.raises(ValueError, match="positive"):
        ContextLimits(max_chunks=0)


# -- scope ----------------------------------------------------------------------------------------


def test_scope_rejects_an_empty_selection_and_mixed_selections() -> None:
    with pytest.raises(InvalidRetrievalScopeError, match="at least one"):
        RetrievalScope(owner_id=OWNER, document_ids=())
    with pytest.raises(InvalidRetrievalScopeError, match="not both"):
        RetrievalScope(owner_id=OWNER, document_ids=(uuid4(),), collection_id=uuid4())


def test_scope_deduplicates_selected_documents_keeping_order() -> None:
    first, second = uuid4(), uuid4()

    scope = RetrievalScope(owner_id=OWNER, document_ids=(first, second, first))

    assert scope.document_ids == (first, second)


def _document(status: ProcessingStatus, owner: UUID = OWNER) -> Document:
    return replace(Factories.document(owner), processing_status=status)


def test_only_the_owners_ready_documents_are_resolved() -> None:
    ready = _document(ProcessingStatus.READY)
    embedding = _document(ProcessingStatus.EMBEDDING)
    failed = _document(ProcessingStatus.FAILED)
    foreign = _document(ProcessingStatus.READY, owner=uuid4())
    scope = RetrievalScope(owner_id=OWNER)

    resolved = resolve_scope(scope, [failed, ready, embedding, foreign])

    assert set(resolved.documents) == {ready.id}
    assert resolved.excluded == 3
    assert not resolved.is_empty
    search = resolved.search_filter()
    assert search.owner_id == OWNER
    assert search.document_ids == (ready.id,)
    assert search.collection_id is None
    assert resolve_scope(scope, [embedding]).is_empty


def test_the_search_filter_orders_document_ids_deterministically() -> None:
    documents = [_document(ProcessingStatus.READY) for _ in range(5)]

    forward = resolve_scope(RetrievalScope(owner_id=OWNER), documents).search_filter()
    backward = resolve_scope(RetrievalScope(owner_id=OWNER), documents[::-1]).search_filter()

    assert forward == backward
    assert forward.document_ids is not None
    assert list(forward.document_ids) == sorted(forward.document_ids, key=lambda d: d.hex)


# -- keyword terms and fusion ---------------------------------------------------------------------


def test_keyword_terms_are_distinct_lower_cased_words_in_order_and_bounded() -> None:
    assert keyword_terms("Refresh tokens: do refresh TOKENS rotate?!") == (
        "refresh",
        "tokens",
        "do",
        "rotate",
    )
    assert keyword_terms("INV-2291 (paid)") == ("inv", "2291", "paid")
    assert keyword_terms("___ ?!? ...") == ()
    assert keyword_terms("Straße 日本語") == ("straße", "日本語")
    assert len(keyword_terms(" ".join(f"w{i}" for i in range(100)))) == MAX_KEYWORD_TERMS
    assert keyword_terms("x" * 65 + " short") == ("short",)


def test_reciprocal_rank_fusion_prefers_hits_found_by_several_rankings() -> None:
    document = _document(ProcessingStatus.READY)
    chunks = [_chunk(document, i, f"text {i}") for i in range(4)]
    semantic = [_hit(chunks[0], 0.9), _hit(chunks[1], 0.8), _hit(chunks[2], 0.7)]
    keyword = [_hit(chunks[3], 5.0), _hit(chunks[1], 4.0)]

    fused = fuse_rankings([semantic, keyword], limit=10)

    assert fused[0].chunk_id == chunks[1].id
    assert {h.chunk_id for h in fused[1:3]} == {chunks[0].id, chunks[3].id}
    assert fused[3].chunk_id == chunks[2].id
    assert fused[0].score == pytest.approx((2 / 62) / (2 / 61))
    assert fused[1].score == pytest.approx(0.5)  # first in one of two rankings
    assert fused[1].score == fused[2].score  # tie between rank-1 hits: broken by vector id
    assert fused[1].id < fused[2].id
    assert fused[0].metadata.embedding_model == "fake-embedding-v1"  # first ranking wins
    assert fuse_rankings([keyword, semantic], limit=10)[0].chunk_id == chunks[1].id
    assert fuse_rankings([semantic, keyword], limit=2) == fused[:2]
    assert fuse_rankings([], limit=5) == []
    assert fuse_rankings([semantic], limit=0) == []


# -- candidate selection --------------------------------------------------------------------------


def _chunk(document: Document, index: int, text: str, *, page: int = 1) -> DocumentChunk:
    return DocumentChunk(
        id=chunk_id(document.id, index),
        document_id=document.id,
        page_id=uuid4(),
        chunk_index=index,
        text=text,
        token_count=len(text.split()),
        metadata={"page_number": page, "content_hash": f"hash:{text}", "section_title": "S"},
    )


def _hit(
    chunk: DocumentChunk,
    score: float,
    *,
    owner: UUID = OWNER,
    content_hash: str | None = None,
) -> SearchHit:
    page = chunk.metadata["page_number"]
    assert isinstance(page, int)
    hash_value = chunk.metadata["content_hash"] if content_hash is None else content_hash
    assert isinstance(hash_value, str)
    return SearchHit(
        id=vector_id_for(chunk.id),
        score=score,
        metadata=VectorMetadata(
            user_id=owner,
            document_id=chunk.document_id,
            chunk_id=chunk.id,
            chunk_index=chunk.chunk_index,
            page_number=page,
            embedding_model="fake-embedding-v1",
            content_hash=hash_value,
        ),
        text="vector copy of the text (never used)",
    )


def test_candidates_become_ranked_evidence_built_from_the_chunk_rows() -> None:
    document = replace(_document(ProcessingStatus.READY), filename="security.pdf")
    scope = resolve_scope(RetrievalScope(owner_id=OWNER), [document])
    low = _chunk(document, 0, "low relevance", page=3)
    high = _chunk(document, 1, "high relevance", page=7)

    selection = select_candidates(
        [_hit(low, 0.2), _hit(high, 0.9)],
        scope=scope,
        chunks={low.id: low, high.id: high},
    )

    assert [e.chunk_id for e in selection.evidence] == [high.id, low.id]
    first = selection.evidence[0]
    assert first.document_id == document.id
    assert first.page_number == 7
    assert first.text == "high relevance"
    assert first.score == 0.9
    assert first.filename == "security.pdf"
    assert first.chunk_index == 1
    assert first.metadata["rank"] == 1
    assert first.metadata["retriever"] == "vector"
    assert first.metadata["section_title"] == "S"
    assert first.metadata["content_hash"] == "hash:high relevance"
    assert first.metadata["embedding_model"] == "fake-embedding-v1"
    assert selection.evidence[1].metadata["rank"] == 2


def test_selection_drops_out_of_scope_stale_and_duplicate_hits() -> None:
    document = _document(ProcessingStatus.READY)
    other = _document(ProcessingStatus.READY)
    scope = resolve_scope(RetrievalScope(owner_id=OWNER), [document])
    kept = _chunk(document, 0, "kept")
    gone = _chunk(document, 2, "gone")
    changed = _chunk(document, 3, "changed")
    twin = _chunk(document, 4, "kept")
    outside = _chunk(other, 0, "outside")
    hits = [
        _hit(kept, 0.8),
        _hit(gone, 0.7),
        _hit(changed, 0.6, content_hash="hash:before the re-index"),
        _hit(twin, 0.5),
        _hit(kept, 0.4),
        _hit(outside, 0.9),
        _hit(kept, 0.95, owner=uuid4()),
    ]

    selection = select_candidates(
        hits, scope=scope, chunks={c.id: c for c in (kept, changed, twin, outside)}
    )

    assert [e.chunk_id for e in selection.evidence] == [kept.id]
    assert selection.out_of_scope == 2
    assert selection.stale == 2
    assert selection.duplicates == 2


def test_a_chunk_under_another_document_than_the_vector_claims_is_stale() -> None:
    document = _document(ProcessingStatus.READY)
    other = _document(ProcessingStatus.READY)
    scope = resolve_scope(RetrievalScope(owner_id=OWNER), [document, other])
    chunk = _chunk(document, 0, "text")
    misfiled = replace(chunk, document_id=other.id)

    selection = select_candidates([_hit(chunk, 0.9)], scope=scope, chunks={chunk.id: misfiled})

    assert selection.evidence == ()
    assert selection.stale == 1


def test_selection_is_deterministic_for_ties_and_input_order() -> None:
    document = _document(ProcessingStatus.READY)
    scope = resolve_scope(RetrievalScope(owner_id=OWNER), [document])
    chunks = [_chunk(document, i, f"text {i}") for i in range(4)]
    hits = [_hit(c, 0.5) for c in chunks]

    forward = select_candidates(hits, scope=scope, chunks={c.id: c for c in chunks})
    backward = select_candidates(hits[::-1], scope=scope, chunks={c.id: c for c in chunks})

    assert forward == backward
    ids = [e.chunk_id for e in forward.evidence]
    assert ids == sorted(ids, key=str)


# -- context assembly -----------------------------------------------------------------------------


def _evidence(document_id: UUID, index: int, score: float, text: str = "t") -> Evidence:
    return Evidence(
        document_id=document_id,
        chunk_id=chunk_id(document_id, index),
        page_number=1,
        text=text,
        score=score,
        metadata={"chunk_index": index},
    )


def test_context_is_numbered_in_rank_order_and_bounded_by_chunk_count() -> None:
    document = uuid4()
    evidence = [_evidence(document, i, 1.0 - i / 10) for i in range(6)]

    context = assemble_context(evidence, limits=ContextLimits(max_chunks=4, max_characters=100))

    assert [item.index for item in context.items] == [1, 2, 3, 4]
    assert [item.evidence.chunk_index for item in context.items] == [0, 1, 2, 3]
    assert context.characters == 4
    assert context.omitted == 2
    assert context.document_ids == (document,)


def test_context_respects_the_character_budget_and_skips_what_does_not_fit() -> None:
    document = uuid4()
    evidence = [
        _evidence(document, 0, 0.9, "a" * 50),
        _evidence(document, 1, 0.8, "b" * 60),  # does not fit after the first
        _evidence(document, 2, 0.7, "c" * 40),  # fits in the remaining budget
    ]

    context = assemble_context(evidence, limits=ContextLimits(max_chunks=10, max_characters=100))

    assert [item.evidence.chunk_index for item in context.items] == [0, 2]
    assert context.characters == 90
    assert context.omitted == 1


def test_context_removes_duplicates_and_is_deterministic() -> None:
    document = uuid4()
    evidence = [
        _evidence(document, 0, 0.9),
        _evidence(document, 0, 0.9),
        _evidence(document, 1, 0.5),
    ]

    context = assemble_context(evidence, limits=ContextLimits())

    assert [item.evidence.chunk_index for item in context.items] == [0, 1]
    assert context.omitted == 0
    assert assemble_context(evidence, limits=ContextLimits()) == context
    # The order given is the rank order; the builder never re-sorts by score.
    reversed_context = assemble_context(evidence[::-1], limits=ContextLimits())
    assert [item.evidence.chunk_index for item in reversed_context.items] == [1, 0]


def test_context_spreads_slots_across_documents_before_filling_with_the_rest() -> None:
    first, second = uuid4(), uuid4()
    evidence = [
        _evidence(first, 0, 0.99),
        _evidence(first, 1, 0.98),
        _evidence(first, 2, 0.97),
        _evidence(second, 0, 0.5),
        _evidence(first, 3, 0.4),
    ]

    context = assemble_context(
        evidence, limits=ContextLimits(max_chunks=4, max_characters=1000, max_chunks_per_document=2)
    )

    chosen = [(item.evidence.document_id, item.evidence.chunk_index) for item in context.items]
    # Two from the first document, the second document's best, then the deferred third chunk of
    # the first document fills the remaining slot; the output is ordered by score again.
    assert chosen == [(first, 0), (first, 1), (first, 2), (second, 0)]
    assert context.omitted == 1


def test_a_single_document_scope_is_not_starved_by_the_per_document_cap() -> None:
    document = uuid4()
    evidence = [_evidence(document, i, 1.0 - i / 10) for i in range(5)]

    context = assemble_context(
        evidence, limits=ContextLimits(max_chunks=5, max_characters=1000, max_chunks_per_document=1)
    )

    assert len(context.items) == 5


def test_an_empty_evidence_list_yields_an_empty_context() -> None:
    context = assemble_context([], limits=ContextLimits())

    assert context.is_empty
    assert context.characters == 0
    assert context.omitted == 0
    assert context.document_ids == ()


def test_context_keeps_the_evidence_rank_order_for_tied_scores() -> None:
    first, second = uuid4(), uuid4()
    # Ranked evidence with equal scores in an order no score-based sort would reproduce.
    evidence = [_evidence(second, 5, 0.5), _evidence(first, 9, 0.5), _evidence(first, 1, 0.5)]

    context = assemble_context(evidence, limits=ContextLimits())

    assert [(item.index, item.evidence.chunk_index) for item in context.items] == [
        (1, 5),
        (2, 9),
        (3, 1),
    ]


def test_deferred_evidence_keeps_its_rank_position_when_it_fits_later() -> None:
    first, second = uuid4(), uuid4()
    evidence = [
        _evidence(first, 0, 0.9),
        _evidence(first, 1, 0.8),  # deferred by the per-document cap, admitted in pass two
        _evidence(second, 0, 0.7),
    ]

    context = assemble_context(
        evidence, limits=ContextLimits(max_chunks=3, max_characters=100, max_chunks_per_document=1)
    )

    assert [(i.evidence.document_id, i.evidence.chunk_index) for i in context.items] == [
        (first, 0),
        (first, 1),
        (second, 0),
    ]
