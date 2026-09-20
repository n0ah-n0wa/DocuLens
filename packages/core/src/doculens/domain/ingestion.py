"""Ingestion rules: what an upload must look like and what extraction produces (§10, §12, §13, §64).

Two families of failure exist and they are kept apart on purpose:

- ``UploadRejectedError`` and its siblings are raised at intake, before anything is stored, and
  answer the client directly (unsupported type, bad signature, too large, quota, duplicate);
- ``PdfRejectedError`` and its subclasses describe a *stored* file that cannot be processed. The
  pipeline turns them into the ``FAILED`` state with the error's safe ``message`` for the user and
  its ``diagnostics`` for the server-side log only (§12).

Everything a PDF says about itself (metadata strings, page text) is untrusted input: it is
sanitised and bounded here before it reaches the database or a user.
"""

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Self
from uuid import UUID

from doculens.domain.documents import DocumentPage
from doculens.domain.errors import ConflictError, DomainError, InvalidInputError

PDF_MIME_TYPE = "application/pdf"
PDF_EXTENSION = ".pdf"
PDF_SIGNATURE = b"%PDF-"
SUPPORTED_MIME_TYPES = frozenset({PDF_MIME_TYPE})
MAX_METADATA_TEXT_LENGTH = 512

# C0 and C1 control characters except tab and newline; NUL is refused by PostgreSQL and the
# rest have no place in text shown to users or handed to a model.
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")


# -- intake failures (answered to the client) ---------------------------------------------------


class UploadRejectedError(InvalidInputError):
    """The upload violates a §10 rule; nothing has been stored."""

    code = "UPLOAD_REJECTED"
    default_message = "The uploaded file is not acceptable."


class UnsupportedFileTypeError(UploadRejectedError):
    code = "UNSUPPORTED_FILE_TYPE"
    default_message = "Only PDF files are supported."


class InvalidFileSignatureError(UploadRejectedError):
    code = "INVALID_FILE_SIGNATURE"
    default_message = "The file does not look like a PDF."


class EmptyUploadError(UploadRejectedError):
    code = "EMPTY_FILE"
    default_message = "The uploaded file is empty."


class FileTooLargeError(UploadRejectedError):
    code = "FILE_TOO_LARGE"
    default_message = "The file exceeds the maximum allowed size."


class DocumentLimitReachedError(InvalidInputError):
    code = "DOCUMENT_LIMIT_REACHED"
    default_message = "The maximum number of documents for this account has been reached."


class DuplicateDocumentError(ConflictError):
    """The same content already exists for this owner (§49; policy provisional, OQ-6)."""

    code = "DUPLICATE_DOCUMENT"
    default_message = "An identical document already exists."

    def __init__(
        self, existing_document_id: UUID | None = None, message: str | None = None
    ) -> None:
        super().__init__(message)
        self.existing_document_id = existing_document_id


# -- processing failures (recorded on the document) --------------------------------------------


class PdfRejectedError(DomainError):
    """A stored file cannot be processed. ``message`` is safe for users; ``diagnostics`` is not."""

    code = "PDF_REJECTED"
    default_message = "The document could not be processed."

    def __init__(self, message: str | None = None, *, diagnostics: str | None = None) -> None:
        super().__init__(message)
        self.diagnostics = diagnostics

    @classmethod
    def for_code(cls, code: str, *, diagnostics: str | None = None) -> "PdfRejectedError":
        """Rebuild the error raised in another process from its stable code."""
        for subclass in _PDF_REJECTIONS:
            if subclass.code == code:
                return subclass(diagnostics=diagnostics)
        return cls(diagnostics=diagnostics)


class CorruptedPdfError(PdfRejectedError):
    code = "CORRUPTED_PDF"
    default_message = "The file is not a readable PDF."


class EncryptedPdfError(PdfRejectedError):
    code = "ENCRYPTED_PDF"
    default_message = "Password-protected PDFs are not supported."


class EmptyPdfError(PdfRejectedError):
    code = "EMPTY_PDF"
    default_message = "The PDF contains no pages."


class TooManyPagesError(PdfRejectedError):
    code = "TOO_MANY_PAGES"
    default_message = "The PDF has more pages than allowed."


class ExtractionTimeoutError(PdfRejectedError):
    code = "EXTRACTION_TIMEOUT"
    default_message = "Text extraction took too long."


class ExtractionFailedError(PdfRejectedError):
    code = "EXTRACTION_FAILED"
    default_message = "Text could not be extracted from the PDF."


class StoredFileMismatchError(PdfRejectedError):
    code = "STORED_FILE_MISMATCH"
    default_message = "The stored file does not match the uploaded document."


class StoredFileMissingError(PdfRejectedError):
    code = "STORED_FILE_MISSING"
    default_message = "The uploaded file is no longer available."


class ContentTooLargeError(PdfRejectedError):
    code = "CONTENT_TOO_LARGE"
    default_message = "The document contains more text than can be processed."


_PDF_REJECTIONS: tuple[type[PdfRejectedError], ...] = (
    CorruptedPdfError,
    EncryptedPdfError,
    EmptyPdfError,
    TooManyPagesError,
    ExtractionTimeoutError,
    ExtractionFailedError,
    StoredFileMismatchError,
    StoredFileMissingError,
    ContentTooLargeError,
)


# -- upload validation (§10) ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class UploadLimits:
    """The §10.2 limits, always injected from configuration, never hard-coded."""

    max_file_size_bytes: int
    max_pages_per_document: int
    max_documents_per_user: int


def has_pdf_signature(data: bytes) -> bool:
    """``%PDF-`` followed by a version digit at the very start of the file (§10.1)."""
    return (
        data.startswith(PDF_SIGNATURE)
        and data[len(PDF_SIGNATURE) : len(PDF_SIGNATURE) + 1].isdigit()
    )


def validate_upload(
    *, filename: str, declared_mime_type: str, data: bytes, limits: UploadLimits
) -> None:
    """The checks that need no parser: extension, declared type, size, signature (§10.1).

    Structural validity (openable, not encrypted, page count) is established by the extractor in
    the worker, where parsing is isolated and bounded (§53, §64).
    """
    if not filename.lower().endswith(PDF_EXTENSION):
        raise UnsupportedFileTypeError
    if declared_mime_type.split(";", 1)[0].strip().lower() not in SUPPORTED_MIME_TYPES:
        raise UnsupportedFileTypeError
    if not data:
        raise EmptyUploadError
    if len(data) > limits.max_file_size_bytes:
        raise FileTooLargeError
    if not has_pdf_signature(data):
        raise InvalidFileSignatureError


# -- extraction results (§13) ---------------------------------------------------------------------


def _without_lone_surrogates(text: str) -> str:
    """Unpaired surrogates cannot be encoded as UTF-8 (PostgreSQL, JSON); replace them."""
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return text.encode("utf-16", "surrogatepass").decode("utf-16", "replace")
    return text


def sanitize_metadata_text(
    value: object, *, max_length: int = MAX_METADATA_TEXT_LENGTH
) -> str | None:
    """Bounded, control-character-free text from an untrusted PDF metadata value."""
    if not isinstance(value, str):
        return None
    value = _without_lone_surrogates(value)
    cleaned = _CONTROL_CHARACTERS.sub("", unicodedata.normalize("NFC", value)).strip()
    if not cleaned:
        return None
    return cleaned[:max_length]


def sanitize_page_text(text: str, *, max_characters: int) -> tuple[str, bool]:
    """Page text with newlines normalised and control characters and lone surrogates removed,
    capped at ``max_characters``; returns ``(text, truncated)``."""
    cleaned = _without_lone_surrogates(text).replace("\r\n", "\n").replace("\r", "\n")
    cleaned = _CONTROL_CHARACTERS.sub("", cleaned)
    if len(cleaned) > max_characters:
        return cleaned[:max_characters], True
    return cleaned, False


@dataclass(frozen=True, slots=True)
class PdfMetadata:
    """Document-level metadata as declared by the file (§13), already sanitised."""

    title: str | None = None
    author: str | None = None
    subject: str | None = None
    keywords: str | None = None
    creator: str | None = None
    producer: str | None = None
    created: str | None = None
    modified: str | None = None
    pdf_version: str | None = None

    @classmethod
    def from_untrusted(cls, raw: Mapping[str, object]) -> Self:
        return cls(
            title=sanitize_metadata_text(raw.get("title")),
            author=sanitize_metadata_text(raw.get("author")),
            subject=sanitize_metadata_text(raw.get("subject")),
            keywords=sanitize_metadata_text(raw.get("keywords")),
            creator=sanitize_metadata_text(raw.get("creator")),
            producer=sanitize_metadata_text(raw.get("producer")),
            created=sanitize_metadata_text(raw.get("created"), max_length=64),
            modified=sanitize_metadata_text(raw.get("modified"), max_length=64),
            pdf_version=sanitize_metadata_text(raw.get("pdf_version"), max_length=16),
        )

    def as_mapping(self) -> dict[str, str]:
        return {
            name: value
            for name, value in (
                ("title", self.title),
                ("author", self.author),
                ("subject", self.subject),
                ("keywords", self.keywords),
                ("creator", self.creator),
                ("producer", self.producer),
                ("created", self.created),
                ("modified", self.modified),
                ("pdf_version", self.pdf_version),
            )
            if value is not None
        }


@dataclass(frozen=True, slots=True)
class PdfInfo:
    """What validation learns without reading page text."""

    page_count: int
    metadata: PdfMetadata


@dataclass(frozen=True, slots=True)
class ExtractedPage:
    """One page, in document order; pages without text are kept, never dropped (§13)."""

    page_number: int
    text: str
    width: float
    height: float
    rotation: int = 0
    truncated: bool = False
    error: str | None = None

    @property
    def has_text(self) -> bool:
        return bool(self.text.strip())

    def to_document_page(self, document_id: UUID, page_id: UUID) -> DocumentPage:
        metadata: dict[str, object] = {
            "has_text": self.has_text,
            "width": self.width,
            "height": self.height,
            "rotation": self.rotation,
        }
        if self.truncated:
            metadata["truncated"] = True
        if self.error is not None:
            metadata["extraction_error"] = self.error
        return DocumentPage(
            id=page_id,
            document_id=document_id,
            page_number=self.page_number,
            extracted_text=self.text,
            character_count=len(self.text),
            metadata=metadata,
        )


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    info: PdfInfo
    pages: tuple[ExtractedPage, ...] = field(default_factory=tuple)

    @property
    def empty_page_count(self) -> int:
        return sum(1 for page in self.pages if not page.has_text)

    @property
    def text_page_count(self) -> int:
        return len(self.pages) - self.empty_page_count


def ordered_pages(pages: Sequence[ExtractedPage]) -> tuple[ExtractedPage, ...]:
    """Pages sorted by number with the numbering verified to be complete and gap-free."""
    ordered = tuple(sorted(pages, key=lambda page: page.page_number))
    expected = list(range(1, len(ordered) + 1))
    if [page.page_number for page in ordered] != expected:
        message = "extracted pages are not numbered 1..n"
        raise ExtractionFailedError(diagnostics=message)
    return ordered
