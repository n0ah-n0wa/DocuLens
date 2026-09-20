"""Document chunking (SPECIFICATIONS.md §7.5, §14, §23, §32).

Chunks are cut from the extracted text of one page at a time, so every chunk carries exactly
one page number (a citation always points at a page, §23). Within a page the splitter prefers
semantic boundaries: a heading starts a new section, paragraphs are packed whole until the chunk
budget is reached, an oversized paragraph is split at sentence ends, and only a sentence that
alone exceeds the budget is cut by token windows. Consecutive chunks of one section overlap by a
configurable number of tokens so that context is not lost at a cut.

Every chunk's ``text`` is a verbatim slice ``page_text[char_start:char_end]`` and its metadata
records those offsets, so a citation can be located in the page again. Chunk identifiers derive
from the document id and the chunk index, so re-chunking a document with the same configuration
produces the same ids and a vector store can upsert instead of duplicating (§32).

Token counts come from a ``Tokenizer`` port. The default implementation is a deterministic,
dependency-free approximation; the embedding provider's own tokenizer can replace it (OQ-16) and
the tokenizer's name travels in chunk metadata so drift is detectable.
"""

import hashlib
import re
from bisect import bisect_left, bisect_right
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from doculens.domain.documents import DocumentChunk, DocumentPage
from doculens.domain.errors import InvalidInputError

CHUNKING_STRATEGY_VERSION = 1
CHUNK_ID_NAMESPACE = uuid5(NAMESPACE_URL, "https://doculens.local/chunks")

Span = tuple[int, int]


class InvalidChunkingConfigError(InvalidInputError):
    code = "INVALID_CHUNKING_CONFIG"
    default_message = "The chunking configuration is not valid."


@dataclass(frozen=True, slots=True)
class ChunkingConfig:
    """§14 configuration in tokens of the configured tokenizer."""

    chunk_size: int
    chunk_overlap: int
    min_chunk_size: int

    def __post_init__(self) -> None:
        if self.chunk_size <= 0:
            message = "CHUNK_SIZE must be positive"
            raise InvalidChunkingConfigError(message)
        if not 0 <= self.chunk_overlap < self.chunk_size:
            message = "CHUNK_OVERLAP must be smaller than CHUNK_SIZE and not negative"
            raise InvalidChunkingConfigError(message)
        if not 0 < self.min_chunk_size <= self.chunk_size:
            message = "MIN_CHUNK_SIZE must be positive and not larger than CHUNK_SIZE"
            raise InvalidChunkingConfigError(message)


class Tokenizer(Protocol):
    @property
    def name(self) -> str:
        """Stable identifier recorded in chunk metadata (drift detection, §32)."""
        ...

    def spans(self, text: str) -> list[Span]:
        """Character spans ``[start, end)`` of every token, in order, non-overlapping."""
        ...


class RegexTokenizer:
    """Deterministic approximation of subword tokenisation without a model dependency.

    A token is a run of up to 32 word characters, a single punctuation mark, or one
    ideographic / kana / hangul character (scripts written without spaces would otherwise count
    as one token per line). Capping word runs keeps a whitespace-free page from counting as a
    single token. Counts are close enough to model tokenizers to bound chunk sizes and are
    identical on every platform and run.
    """

    name = "regex-v1"
    _pattern = re.compile(
        r"[⺀-⿿　-ヿ㐀-䶿一-鿿가-힯豈-﫿]"
        r"|\w{1,32}"
        r"|[^\w\s]",
        re.UNICODE,
    )

    def spans(self, text: str) -> list[Span]:
        # Format and control characters (zero-width spaces, BOMs) are not tokens.
        return [m.span() for m in self._pattern.finditer(text) if m.group().isprintable()]


def chunk_id(document_id: UUID, chunk_index: int) -> UUID:
    """Stable identity of the ``chunk_index``-th chunk of a document (§32)."""
    return uuid5(CHUNK_ID_NAMESPACE, f"{document_id}:{chunk_index}")


# -- page structure -------------------------------------------------------------------------------

_BLANK_LINE = re.compile(r"\n[ \t\r\f\v]*\n")
_SENTENCE_END = re.compile(r"(?<=[.!?\u3002\uff01\uff1f])\s+")
_NUMBERED_HEADING = re.compile(r"^(?:\d+(?:\.\d+)*[.)]?|[IVXLC]+\.|[A-Z]\.)\s+\S")
MAX_HEADING_WORDS = 12
MAX_HEADING_CHARS = 120
MAX_TITLE_CASE_WORDS = 8
MIN_UPPER_CASE_LETTERS = 2
_SENTENCE_PUNCTUATION = ".,;!?"


@dataclass(frozen=True, slots=True)
class Paragraph:
    start: int
    end: int
    is_heading: bool


@dataclass(frozen=True, slots=True)
class Section:
    title: str | None
    paragraphs: tuple[Paragraph, ...]


def looks_like_heading(line: str) -> bool:
    """A short single line without sentence punctuation: numbered, upper-case or title-case."""
    stripped = line.strip()
    words = stripped.split()
    letters = [char for char in stripped if char.isalpha()]
    shaped_like_a_line = (
        bool(stripped)
        and "\n" not in stripped
        and len(stripped) <= MAX_HEADING_CHARS
        and len(words) <= MAX_HEADING_WORDS
        and not (stripped[-1] in _SENTENCE_PUNCTUATION and not stripped.endswith(":"))
        and len(letters) >= MIN_UPPER_CASE_LETTERS
    )
    if not shaped_like_a_line:
        return False
    if _NUMBERED_HEADING.match(stripped):
        return True
    if len(letters) >= MIN_UPPER_CASE_LETTERS and all(char.isupper() for char in letters):
        return True
    alphabetic_words = [word for word in words if word[0].isalpha()]
    title_cased = sum(1 for word in alphabetic_words if word[0].isupper())
    return (
        bool(alphabetic_words)
        and title_cased == len(alphabetic_words)
        and len(words) <= MAX_TITLE_CASE_WORDS
    )


def paragraphs_of(text: str) -> list[Paragraph]:
    """Blank-line separated blocks with their offsets, surrounding whitespace excluded."""
    result: list[Paragraph] = []
    position = 0
    for match in [*_BLANK_LINE.finditer(text), None]:
        end = match.start() if match is not None else len(text)
        block = text[position:end]
        leading = len(block) - len(block.lstrip())
        trailing = len(block) - len(block.rstrip())
        if leading + trailing < len(block):
            start, stop = position + leading, end - trailing
            result.append(Paragraph(start, stop, looks_like_heading(text[start:stop])))
        position = match.end() if match is not None else end
    return result


def sections_of(text: str) -> list[Section]:
    """Paragraph groups delimited by headings; the heading is the first paragraph of its group."""
    sections: list[Section] = []
    title: str | None = None
    current: list[Paragraph] = []
    for paragraph in paragraphs_of(text):
        if paragraph.is_heading:
            if current:
                sections.append(Section(title, tuple(current)))
            title = text[paragraph.start : paragraph.end].strip()
            current = [paragraph]
        else:
            current.append(paragraph)
    if current:
        sections.append(Section(title, tuple(current)))
    return sections


# -- splitting ------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Piece:
    """A chunk-to-be inside one page: a text span plus how it was formed."""

    start: int
    end: int
    section_title: str | None
    split: str  # paragraph | sentence | window
    overlap_tokens: int = 0


@dataclass(slots=True)
class _PageTokens:
    """Token spans of one page with offset lookups."""

    spans: list[Span]
    starts: list[int] = field(init=False)
    ends: list[int] = field(init=False)

    def __post_init__(self) -> None:
        self.starts = [start for start, _ in self.spans]
        self.ends = [end for _, end in self.spans]

    def first_at_or_after(self, position: int) -> int:
        return bisect_left(self.starts, position)

    def last_before(self, position: int) -> int:
        return bisect_right(self.ends, position) - 1

    def count(self, start: int, end: int) -> int:
        return max(0, self.last_before(end) - self.first_at_or_after(start) + 1)


def chunk_page_text(text: str, config: ChunkingConfig, tokenizer: Tokenizer) -> list[Piece]:
    """Deterministic pieces of one page's text; empty for text without tokens.

    ``chunk_size`` is a hard maximum that includes the overlap prepended from the previous
    piece, so every piece fits an embedding budget of ``chunk_size`` tokens.
    """
    tokens = _PageTokens(tokenizer.spans(text))
    if not tokens.spans:
        return []
    pieces: list[Piece] = []
    for section in _fold_tiny_sections(sections_of(text), tokens, config):
        packer = _Packer(text, section.title, tokens, config)
        for paragraph in section.paragraphs:
            packer.add_paragraph(paragraph)
        pieces.extend(packer.finish())
    return pieces


def _fold_tiny_sections(
    sections: list[Section], tokens: _PageTokens, config: ChunkingConfig
) -> list[Section]:
    """A section below the minimum (a lone heading, a stray line) joins the next section."""
    folded: list[Section] = []
    carry: list[Paragraph] = []
    for index, section in enumerate(sections):
        paragraphs = [*carry, *section.paragraphs]
        size = sum(tokens.count(p.start, p.end) for p in paragraphs)
        if size < config.min_chunk_size and index < len(sections) - 1:
            carry = paragraphs
            continue
        folded.append(Section(section.title, tuple(paragraphs)))
        carry = []
    if carry:  # every section was tiny: keep the content as one section
        folded.append(Section(None, tuple(carry)))
    return folded


class _Packer:
    """Packs one section into pieces, applying overlap as each piece is opened.

    The budget of a piece is ``chunk_size`` minus the overlap it will receive from its
    predecessor, so the final token count never exceeds ``chunk_size``.
    """

    def __init__(
        self, text: str, title: str | None, tokens: _PageTokens, config: ChunkingConfig
    ) -> None:
        self._text = text
        self._title = title
        self._tokens = tokens
        self._config = config
        self._pieces: list[Piece] = []
        self._spans: list[Span] = []
        self._split = "paragraph"
        self._packed = 0

    # -- input -------------------------------------------------------------------------------

    def add_paragraph(self, paragraph: Paragraph) -> None:
        size = self._tokens.count(paragraph.start, paragraph.end)
        if size == 0:
            return
        if size > self._config.chunk_size:
            self._flush()
            self._add_oversized_paragraph(paragraph)
            return
        self._add_span((paragraph.start, paragraph.end), size, "paragraph")

    def _add_oversized_paragraph(self, paragraph: Paragraph) -> None:
        for start, end in _sentence_spans(self._text, paragraph.start, paragraph.end):
            size = self._tokens.count(start, end)
            if size == 0:
                continue
            if size > self._config.chunk_size:
                self._flush()
                self._pieces.extend(_windows(start, end, self._title, self._tokens, self._config))
                continue
            self._add_span((start, end), size, "sentence")
        self._flush()

    def _add_span(self, span: Span, size: int, split: str) -> None:
        if self._spans and (self._packed + size > self._budget() or self._split != split):
            self._flush()
        if not self._spans:
            self._split = split
        self._spans.append(span)
        self._packed += size

    # -- output ------------------------------------------------------------------------------

    def finish(self) -> list[Piece]:
        self._flush()
        return self._merge_small_tail()

    def _budget(self) -> int:
        return self._config.chunk_size - self._next_overlap()

    def _next_overlap(self) -> int:
        """Tokens the next piece will borrow from the last emitted piece of this section."""
        if self._config.chunk_overlap == 0 or not self._pieces:
            return 0
        previous = self._pieces[-1]
        available = self._tokens.count(previous.start, previous.end)
        return min(self._config.chunk_overlap, available // 2)

    def _flush(self) -> None:
        if not self._spans:
            return
        start, end = self._spans[0][0], self._spans[-1][1]
        overlap = self._next_overlap()
        if overlap:
            previous_last = self._tokens.last_before(self._pieces[-1].end)
            start = self._tokens.starts[previous_last - overlap + 1]
        self._pieces.append(Piece(start, end, self._title, self._split, overlap_tokens=overlap))
        self._spans, self._packed = [], 0

    def _merge_small_tail(self) -> list[Piece]:
        """A trailing piece below the minimum joins its predecessor when the result fits."""
        pieces = self._pieces
        if len(pieces) < 2:  # noqa: PLR2004 - a lone piece has no predecessor
            return pieces
        tail, previous = pieces[-1], pieces[-2]
        tokens = self._tokens
        if (
            tokens.count(tail.start, tail.end) >= self._config.min_chunk_size
            or tail.split == "window"
            or previous.split == "window"
            or tokens.count(previous.start, tail.end) > self._config.chunk_size
        ):
            return pieces
        merged = Piece(
            previous.start,
            tail.end,
            previous.section_title,
            previous.split,
            overlap_tokens=previous.overlap_tokens,
        )
        return [*pieces[:-2], merged]


def _sentence_spans(text: str, start: int, end: int) -> list[Span]:
    spans: list[Span] = []
    position = start
    for match in _SENTENCE_END.finditer(text, start, end):
        if match.start() > position:
            spans.append((position, match.start()))
        position = match.end()
    if position < end:
        spans.append((position, end))
    return spans


def _windows(
    start: int, end: int, title: str | None, tokens: _PageTokens, config: ChunkingConfig
) -> list[Piece]:
    """Fixed token windows with the configured overlap over one oversized sentence."""
    first = tokens.first_at_or_after(start)
    last = tokens.last_before(end)
    step = config.chunk_size - config.chunk_overlap
    pieces: list[Piece] = []
    position = first
    while position <= last:
        stop = min(position + config.chunk_size - 1, last)
        overlap = 0 if position == first else min(config.chunk_overlap, stop - position + 1)
        pieces.append(
            Piece(
                tokens.starts[position], tokens.ends[stop], title, "window", overlap_tokens=overlap
            )
        )
        if stop == last:
            break
        position += step
    return pieces


# -- document level -------------------------------------------------------------------------------


def chunk_document(
    document_id: UUID,
    pages: Sequence[DocumentPage],
    config: ChunkingConfig,
    tokenizer: Tokenizer,
) -> list[DocumentChunk]:
    """All chunks of a document in page order with document-wide, stable indexes and ids."""
    chunks: list[DocumentChunk] = []
    configuration = {
        "chunk_size": config.chunk_size,
        "chunk_overlap": config.chunk_overlap,
        "min_chunk_size": config.min_chunk_size,
        "tokenizer": tokenizer.name,
        "version": CHUNKING_STRATEGY_VERSION,
    }
    for page in sorted(pages, key=lambda p: p.page_number):
        text = page.extracted_text
        for piece in chunk_page_text(text, config, tokenizer):
            index = len(chunks)
            chunk_text = text[piece.start : piece.end]
            metadata: dict[str, object] = {
                "page_number": page.page_number,
                "char_start": piece.start,
                "char_end": piece.end,
                "split": piece.split,
                "overlap_tokens": piece.overlap_tokens,
                "content_hash": hashlib.sha256(chunk_text.encode("utf-8")).hexdigest(),
                "chunking": configuration,
            }
            if piece.section_title is not None:
                metadata["section_title"] = piece.section_title
            chunks.append(
                DocumentChunk(
                    id=chunk_id(document_id, index),
                    document_id=document_id,
                    page_id=page.id,
                    chunk_index=index,
                    text=chunk_text,
                    token_count=len(tokenizer.spans(chunk_text)),
                    metadata=metadata,
                )
            )
    return chunks


__all__ = [
    "CHUNKING_STRATEGY_VERSION",
    "ChunkingConfig",
    "InvalidChunkingConfigError",
    "Paragraph",
    "Piece",
    "RegexTokenizer",
    "Section",
    "Tokenizer",
    "chunk_document",
    "chunk_id",
    "chunk_page_text",
    "looks_like_heading",
    "paragraphs_of",
    "sections_of",
]
