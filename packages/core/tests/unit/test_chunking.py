"""Chunking (§14): configurable, deterministic, page-aware, boundary-preferring, lossless."""

import random
from collections.abc import Sequence
from uuid import UUID, uuid4

import pytest

from doculens.domain.chunking import (
    ChunkingConfig,
    InvalidChunkingConfigError,
    RegexTokenizer,
    chunk_document,
    chunk_id,
    chunk_page_text,
    looks_like_heading,
    paragraphs_of,
    sections_of,
)
from doculens.domain.documents import DocumentChunk, DocumentPage

pytestmark = pytest.mark.unit

TOKENIZER = RegexTokenizer()
DOCUMENT_ID = UUID("11111111-1111-4111-8111-111111111111")
SMALL = ChunkingConfig(chunk_size=20, chunk_overlap=4, min_chunk_size=5)
NO_OVERLAP = ChunkingConfig(chunk_size=20, chunk_overlap=0, min_chunk_size=5)


def words(count: int, prefix: str = "w") -> str:
    return " ".join(f"{prefix}{i}" for i in range(count))


def page(number: int, text: str, page_id: UUID | None = None) -> DocumentPage:
    return DocumentPage(
        id=page_id or uuid4(),
        document_id=DOCUMENT_ID,
        page_number=number,
        extracted_text=text,
        character_count=len(text),
        metadata={},
    )


def tokens(text: str) -> int:
    return len(TOKENIZER.spans(text))


def assert_covers(chunks: Sequence[DocumentChunk], text: str) -> None:
    """Every token of the page text is inside at least one chunk (nothing is dropped)."""
    covered = [False] * len(text)
    for chunk in chunks:
        start, end = chunk.metadata["char_start"], chunk.metadata["char_end"]
        assert isinstance(start, int) and isinstance(end, int)  # noqa: PT018 - one condition
        assert text[start:end] == chunk.text
        for position in range(start, end):
            covered[position] = True
    for start, end in TOKENIZER.spans(text):
        assert all(covered[start:end]), f"token {text[start:end]!r} at {start} is not covered"


# -- configuration and tokenizer ------------------------------------------------------------------


@pytest.mark.parametrize(
    "arguments",
    [
        {"chunk_size": 0, "chunk_overlap": 0, "min_chunk_size": 1},
        {"chunk_size": 10, "chunk_overlap": 10, "min_chunk_size": 1},
        {"chunk_size": 10, "chunk_overlap": -1, "min_chunk_size": 1},
        {"chunk_size": 10, "chunk_overlap": 2, "min_chunk_size": 0},
        {"chunk_size": 10, "chunk_overlap": 2, "min_chunk_size": 11},
    ],
)
def test_incoherent_configurations_are_refused(arguments: dict[str, int]) -> None:
    with pytest.raises(InvalidChunkingConfigError):
        ChunkingConfig(**arguments)


def test_the_tokenizer_counts_words_punctuation_and_ideographs_deterministically() -> None:
    assert tokens("Hello, world!") == 4
    assert tokens("naïve café") == 2
    assert tokens("東京都に行きました") == 9  # one token per ideograph / kana
    assert tokens("") == 0
    assert tokens("   \n\t ") == 0
    assert tokens("a" * 100) == 4  # word runs are capped at 32 characters
    assert TOKENIZER.spans("a b") == [(0, 1), (2, 3)]
    assert TOKENIZER.name == "regex-v1"


# -- structure detection --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("1. Introduction", True),
        ("2.3.1 Scope of work", True),
        ("EXECUTIVE SUMMARY", True),
        ("Quarterly Results Overview", True),
        ("Appendix A:", True),
        ("This is an ordinary sentence that ends with a full stop.", False),
        ("revenue grew twelve percent in the third quarter of the year", False),
        ("", False),
        ("Two lines\nhere", False),
        ("X", False),
    ],
)
def test_heading_detection(line: str, expected: bool) -> None:  # noqa: FBT001 - parametrised
    assert looks_like_heading(line) is expected


def test_paragraphs_and_sections_keep_offsets_into_the_original_text() -> None:
    text = "\n\nIntro\n\nFirst paragraph\nstill first.\n\n\n  Second paragraph.  \n"

    paragraphs = paragraphs_of(text)

    assert [text[p.start : p.end] for p in paragraphs] == [
        "Intro",
        "First paragraph\nstill first.",
        "Second paragraph.",
    ]
    assert [p.is_heading for p in paragraphs] == [True, False, False]
    sections = sections_of(text)
    assert [s.title for s in sections] == ["Intro"]
    assert len(sections[0].paragraphs) == 3


# -- short documents and empty text ---------------------------------------------------------------


def test_a_short_page_becomes_a_single_chunk_with_the_whole_text() -> None:
    text = "A short page with one sentence."
    chunks = chunk_document(DOCUMENT_ID, [page(1, text)], SMALL, TOKENIZER)

    (chunk,) = chunks
    assert chunk.text == text
    assert chunk.chunk_index == 0
    assert chunk.document_id == DOCUMENT_ID
    assert chunk.token_count == tokens(text)
    assert chunk.metadata["page_number"] == 1
    assert chunk.metadata["split"] == "paragraph"
    assert chunk.metadata["overlap_tokens"] == 0
    assert chunk.metadata["chunking"] == {
        "chunk_size": 20,
        "chunk_overlap": 4,
        "min_chunk_size": 5,
        "tokenizer": "regex-v1",
        "version": 1,
    }
    assert chunk.vector_id is None


def test_a_page_smaller_than_the_minimum_is_still_kept() -> None:
    chunks = chunk_document(DOCUMENT_ID, [page(1, "Tiny.")], SMALL, TOKENIZER)

    assert [c.text for c in chunks] == ["Tiny."]


@pytest.mark.parametrize("text", ["", "   ", "\n\n\t\n", "\u200b"])
def test_text_without_tokens_yields_no_chunks(text: str) -> None:
    assert chunk_page_text(text, SMALL, TOKENIZER) == []
    assert chunk_document(DOCUMENT_ID, [page(1, text)], SMALL, TOKENIZER) == []


def test_a_document_without_pages_has_no_chunks() -> None:
    assert chunk_document(DOCUMENT_ID, [], SMALL, TOKENIZER) == []


# -- paragraphs -----------------------------------------------------------------------------------


def test_whole_paragraphs_are_packed_until_the_budget_is_reached() -> None:
    text = "\n\n".join([words(8, "a"), words(8, "b"), words(8, "c"), words(8, "d")])

    chunks = chunk_document(DOCUMENT_ID, [page(1, text)], NO_OVERLAP, TOKENIZER)

    assert [c.text for c in chunks] == [
        words(8, "a") + "\n\n" + words(8, "b"),
        words(8, "c") + "\n\n" + words(8, "d"),
    ]
    assert all(c.token_count <= NO_OVERLAP.chunk_size for c in chunks)
    assert all(c.metadata["split"] == "paragraph" for c in chunks)
    assert_covers(chunks, text)


def test_a_small_trailing_paragraph_joins_its_predecessor_when_it_fits() -> None:
    text = words(15, "a") + "\n\n" + "tail one."

    chunks = chunk_document(DOCUMENT_ID, [page(1, text)], NO_OVERLAP, TOKENIZER)

    assert len(chunks) == 1
    assert chunks[0].text == text
    assert chunks[0].token_count == 18

    # The budget is a hard maximum: a tail that would overflow it stays a chunk of its own.
    overflowing = words(18, "a") + "\n\n" + "tail one."
    chunks = chunk_document(DOCUMENT_ID, [page(1, overflowing)], NO_OVERLAP, TOKENIZER)
    assert [c.token_count for c in chunks] == [18, 3]


def test_chunk_size_is_a_hard_maximum_that_includes_the_overlap() -> None:
    text = "\n\n".join(words(9, f"p{i}") for i in range(12))
    config = ChunkingConfig(chunk_size=20, chunk_overlap=6, min_chunk_size=3)

    chunks = chunk_document(DOCUMENT_ID, [page(1, text)], config, TOKENIZER)

    assert all(c.token_count <= config.chunk_size for c in chunks)
    assert all(c.metadata["overlap_tokens"] == 6 for c in chunks[1:])
    assert_covers(chunks, text)


def test_whitespace_free_text_is_still_bounded() -> None:
    text = "x" * 5_000  # one run, no whitespace: 32-character tokens, never a single token
    config = ChunkingConfig(chunk_size=20, chunk_overlap=2, min_chunk_size=3)

    chunks = chunk_document(DOCUMENT_ID, [page(1, text)], config, TOKENIZER)

    assert len(chunks) > 1
    assert all(c.token_count <= config.chunk_size for c in chunks)
    assert all(len(c.text) <= config.chunk_size * 32 for c in chunks)
    assert_covers(chunks, text)


def test_paragraphs_without_tokens_produce_no_chunks_of_their_own() -> None:
    text = words(5, "a") + "\n\n\u200b\n\n" + words(5, "b")

    chunks = chunk_document(DOCUMENT_ID, [page(1, text)], NO_OVERLAP, TOKENIZER)

    assert len(chunks) == 1
    assert all(c.token_count > 0 for c in chunks)


def test_overlap_repeats_the_tail_of_the_previous_chunk() -> None:
    text = words(12, "a") + "\n\n" + words(12, "b")

    first, second = chunk_document(DOCUMENT_ID, [page(1, text)], SMALL, TOKENIZER)

    assert first.text == words(12, "a")
    assert second.metadata["overlap_tokens"] == 4
    assert second.text.startswith("a8 a9 a10 a11\n\n")
    assert second.text.endswith(words(12, "b"))
    assert second.token_count == 16


# -- long pages -----------------------------------------------------------------------------------


def test_an_oversized_paragraph_is_split_at_sentence_boundaries() -> None:
    sentences = [f"Sentence number {i} has exactly six tokens." for i in range(8)]
    text = " ".join(sentences)  # 8 sentences of 8 tokens each, no blank lines

    chunks = chunk_document(DOCUMENT_ID, [page(1, text)], NO_OVERLAP, TOKENIZER)

    assert len(chunks) == 4
    assert all(c.metadata["split"] == "sentence" for c in chunks)
    assert all(c.text.endswith(".") for c in chunks)
    assert all(c.token_count <= NO_OVERLAP.chunk_size for c in chunks)
    assert_covers(chunks, text)


def test_a_single_sentence_over_budget_is_cut_into_overlapping_token_windows() -> None:
    text = words(70)  # one "sentence" of 70 tokens, no punctuation

    chunks = chunk_document(DOCUMENT_ID, [page(1, text)], SMALL, TOKENIZER)

    assert all(c.metadata["split"] == "window" for c in chunks)
    assert all(c.token_count <= SMALL.chunk_size for c in chunks)
    assert chunks[0].text == words(20)
    assert chunks[1].text.startswith("w16 w17 w18 w19 w20")
    assert [c.metadata["overlap_tokens"] for c in chunks][1:] == [4] * (len(chunks) - 1)
    assert chunks[-1].text.endswith("w69")
    assert_covers(chunks, text)


def test_a_very_large_section_is_chunked_within_budget_and_fully_covered() -> None:
    paragraphs = [f"Paragraph {p}. " + words(37, f"p{p}w") + "." for p in range(400)]
    text = "\n\n".join(paragraphs)  # ~16k tokens
    config = ChunkingConfig(chunk_size=512, chunk_overlap=64, min_chunk_size=64)

    chunks = chunk_document(DOCUMENT_ID, [page(1, text)], config, TOKENIZER)

    assert 30 <= len(chunks) <= 45
    assert all(c.token_count <= config.chunk_size for c in chunks)
    assert all(c.metadata["overlap_tokens"] == 64 for c in chunks[1:])
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))
    assert_covers(chunks, text)


# -- multiple pages and sections ------------------------------------------------------------------


def test_chunks_never_span_pages_and_indexes_continue_across_them() -> None:
    first_id, second_id = uuid4(), uuid4()
    pages = [
        page(2, words(30, "b"), second_id),
        page(1, words(30, "a"), first_id),
    ]

    chunks = chunk_document(DOCUMENT_ID, pages, NO_OVERLAP, TOKENIZER)

    assert [c.metadata["page_number"] for c in chunks] == [1, 1, 2, 2]
    assert [c.page_id for c in chunks] == [first_id, first_id, second_id, second_id]
    assert [c.chunk_index for c in chunks] == [0, 1, 2, 3]
    assert all("a" in c.text and "b" not in c.text for c in chunks[:2])
    assert all("b" in c.text and "a" not in c.text for c in chunks[2:])


def test_headings_start_sections_and_are_recorded_on_their_chunks() -> None:
    text = (
        "1. Introduction\n\n"
        + words(10, "intro")
        + "\n\n2. Methods\n\n"
        + words(8, "method")
        + "\n\n"
        + words(8, "more")
    )

    chunks = chunk_document(DOCUMENT_ID, [page(1, text)], NO_OVERLAP, TOKENIZER)

    assert [c.metadata["section_title"] for c in chunks] == ["1. Introduction", "2. Methods"]
    assert chunks[0].text.startswith("1. Introduction")
    assert chunks[1].text.startswith("2. Methods")
    assert "method" in chunks[1].text and "more" in chunks[1].text  # noqa: PT018 - one idea
    assert_covers(chunks, text)


def test_overlap_does_not_cross_a_section_boundary() -> None:
    text = "Alpha Section\n\n" + words(12, "a") + "\n\nBeta Section\n\n" + words(12, "b")

    chunks = chunk_document(DOCUMENT_ID, [page(1, text)], SMALL, TOKENIZER)

    assert [c.metadata["overlap_tokens"] for c in chunks] == [0, 0]
    assert chunks[1].text.startswith("Beta Section")


def test_a_lone_heading_is_folded_into_the_section_it_introduces() -> None:
    text = "Preface\n\nDetails\n\n" + words(12, "d")

    chunks = chunk_document(DOCUMENT_ID, [page(1, text)], SMALL, TOKENIZER)

    assert len(chunks) == 1
    assert chunks[0].text == text
    assert_covers(chunks, text)


# -- unicode --------------------------------------------------------------------------------------


def test_unicode_text_is_chunked_by_characters_it_understands() -> None:
    japanese = "東京都に行きました。" * 6  # 60 tokens: 10 per sentence, no spaces
    config = ChunkingConfig(chunk_size=25, chunk_overlap=0, min_chunk_size=5)

    chunks = chunk_document(DOCUMENT_ID, [page(1, japanese)], config, TOKENIZER)

    assert len(chunks) == 3
    assert all(c.token_count <= 25 for c in chunks)
    assert "".join(c.text for c in chunks).replace("\n", "") == japanese.replace(" ", "")
    assert_covers(chunks, japanese)


def test_offsets_stay_correct_with_emoji_combining_marks_and_rtl_text() -> None:
    text = "Café 👩‍🔬 résumé naïve.\n\nשלום עולם, זה מבחן.\n\nZażółć gęślą jaźń."  # noqa: RUF001 - Hebrew

    chunks = chunk_document(DOCUMENT_ID, [page(1, text)], SMALL, TOKENIZER)

    assert_covers(chunks, text)
    for chunk in chunks:
        assert chunk.token_count == tokens(chunk.text)
        assert (
            chunk.metadata["content_hash"]
            == __import__("hashlib").sha256(chunk.text.encode("utf-8")).hexdigest()
        )


# -- determinism and identity ---------------------------------------------------------------------


def test_output_is_identical_across_runs_and_input_orderings() -> None:
    random.seed(7)
    paragraphs = [words(random.randint(3, 40), f"p{i}") for i in range(40)]  # noqa: S311 - test data
    pages = [page(n, "\n\n".join(paragraphs[n * 8 : (n + 1) * 8]), uuid4()) for n in range(5)]
    config = ChunkingConfig(chunk_size=30, chunk_overlap=5, min_chunk_size=6)

    first = chunk_document(DOCUMENT_ID, pages, config, TOKENIZER)
    shuffled = list(pages)
    random.shuffle(shuffled)
    second = chunk_document(DOCUMENT_ID, shuffled, config, TOKENIZER)

    assert first == second
    assert [c.id for c in first] == [chunk_id(DOCUMENT_ID, i) for i in range(len(first))]
    assert len({c.id for c in first}) == len(first)


def test_chunk_ids_depend_on_the_document_and_index_only() -> None:
    other = UUID("22222222-2222-4222-8222-222222222222")

    assert chunk_id(DOCUMENT_ID, 3) == chunk_id(DOCUMENT_ID, 3)
    assert chunk_id(DOCUMENT_ID, 3) != chunk_id(DOCUMENT_ID, 4)
    assert chunk_id(DOCUMENT_ID, 3) != chunk_id(other, 3)
    assert chunk_id(DOCUMENT_ID, 0).version == 5


def test_changing_the_configuration_changes_the_recorded_fingerprint_not_the_id_scheme() -> None:
    text = words(30)
    loose = chunk_document(DOCUMENT_ID, [page(1, text)], NO_OVERLAP, TOKENIZER)
    tight = chunk_document(
        DOCUMENT_ID,
        [page(1, text)],
        ChunkingConfig(chunk_size=10, chunk_overlap=0, min_chunk_size=2),
        TOKENIZER,
    )

    assert len(loose) == 2
    assert len(tight) == 3
    assert loose[0].id == tight[0].id  # index 0 of the same document
    assert loose[0].metadata["chunking"] != tight[0].metadata["chunking"]
    assert loose[0].metadata["content_hash"] != tight[0].metadata["content_hash"]
