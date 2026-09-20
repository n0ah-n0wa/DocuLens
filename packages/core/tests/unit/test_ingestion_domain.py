"""Upload rules, untrusted-text sanitisation and extraction value objects (§10, §13)."""

from uuid import uuid4

import pytest

from doculens.domain.errors import ConflictError, InvalidInputError
from doculens.domain.ingestion import (
    CorruptedPdfError,
    DuplicateDocumentError,
    EmptyUploadError,
    ExtractedPage,
    ExtractionFailedError,
    FileTooLargeError,
    InvalidFileSignatureError,
    PdfMetadata,
    PdfRejectedError,
    UnsupportedFileTypeError,
    UploadLimits,
    has_pdf_signature,
    ordered_pages,
    sanitize_metadata_text,
    sanitize_page_text,
    validate_upload,
)

pytestmark = pytest.mark.unit

LIMITS = UploadLimits(max_file_size_bytes=1024, max_pages_per_document=5, max_documents_per_user=3)
PDF = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<< >>\nendobj\n%%EOF\n"


def _validate(**overrides: object) -> None:
    arguments: dict[str, object] = {
        "filename": "report.pdf",
        "declared_mime_type": "application/pdf",
        "data": PDF,
        "limits": LIMITS,
    }
    arguments.update(overrides)
    validate_upload(**arguments)  # type: ignore[arg-type]


def test_a_well_formed_upload_passes() -> None:
    _validate()
    _validate(filename="REPORT.PDF")
    _validate(declared_mime_type="Application/PDF; charset=binary")


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"filename": "report.txt"}, UnsupportedFileTypeError),
        ({"filename": "report.pdf.exe"}, UnsupportedFileTypeError),
        ({"declared_mime_type": "text/plain"}, UnsupportedFileTypeError),
        ({"declared_mime_type": "application/x-pdf"}, UnsupportedFileTypeError),
        ({"data": b""}, EmptyUploadError),
        ({"data": b"%PDF-1.7" + b"x" * 2000}, FileTooLargeError),
        ({"data": b"%PDF" + b"x" * 40}, InvalidFileSignatureError),
        ({"data": b"garbage" * 10}, InvalidFileSignatureError),
        ({"data": b"\xef\xbb\xbf%PDF-1.4 bom first"}, InvalidFileSignatureError),
    ],
)
def test_uploads_that_break_a_rule_are_refused_with_a_stable_code(
    overrides: dict[str, object], error: type[InvalidInputError]
) -> None:
    with pytest.raises(error) as excinfo:
        _validate(**overrides)
    assert excinfo.value.code == error.code
    assert isinstance(excinfo.value, InvalidInputError)


def test_the_signature_needs_a_version_digit() -> None:
    assert has_pdf_signature(b"%PDF-1.4")
    assert has_pdf_signature(b"%PDF-2.0\n")
    assert not has_pdf_signature(b"%PDF-")
    assert not has_pdf_signature(b"%PDF-x")
    assert not has_pdf_signature(b"")


def test_duplicate_is_a_conflict_carrying_the_existing_id() -> None:
    existing = uuid4()
    error = DuplicateDocumentError(existing)
    assert isinstance(error, ConflictError)
    assert error.code == "DUPLICATE_DOCUMENT"
    assert error.existing_document_id == existing
    assert str(existing) not in error.message


def test_processing_rejections_round_trip_by_code_and_keep_diagnostics_private() -> None:
    rebuilt = PdfRejectedError.for_code("CORRUPTED_PDF", diagnostics="FileDataError: bad xref")
    assert isinstance(rebuilt, CorruptedPdfError)
    assert rebuilt.diagnostics == "FileDataError: bad xref"
    assert "xref" not in rebuilt.message

    unknown = PdfRejectedError.for_code("SOMETHING_NEW")
    assert type(unknown) is PdfRejectedError


def test_metadata_text_is_normalised_bounded_and_control_free() -> None:
    assert (
        sanitize_metadata_text("  Title\x00 with\x07 control \x1f chars ")
        == "Title with control  chars"
    )
    assert sanitize_metadata_text("x" * 600) == "x" * 512
    assert sanitize_metadata_text("   ") is None
    assert sanitize_metadata_text(None) is None
    assert sanitize_metadata_text(42) is None
    assert sanitize_metadata_text("é") == "é"  # NFC


def test_page_text_drops_control_characters_normalises_newlines_and_reports_truncation() -> None:
    assert sanitize_page_text("a\x00b\r\nc\rd", max_characters=100) == ("ab\nc\nd", False)
    assert sanitize_page_text("abcdef", max_characters=3) == ("abc", True)
    assert sanitize_page_text("x\x1b[31mred\x07\x9f\ty", max_characters=100) == (
        "x[31mred\ty",
        False,
    )
    assert sanitize_page_text("lone \ud83d surrogate", max_characters=100) == (
        "lone \ufffd surrogate",
        False,
    )
    assert sanitize_metadata_text("t\ud800itle") == "t\ufffditle"


def test_pdf_metadata_is_built_from_untrusted_input_and_serialised_without_gaps() -> None:
    metadata = PdfMetadata.from_untrusted(
        {
            "title": "Q3\x00 report",
            "author": "",
            "creator": None,
            "unknown": "ignored",
            "format": "x",
        }
    )

    assert metadata.title == "Q3 report"
    assert metadata.author is None
    assert metadata.as_mapping() == {"title": "Q3 report"}


def test_extracted_pages_become_document_pages_that_record_their_condition() -> None:
    document_id, page_id = uuid4(), uuid4()
    page = ExtractedPage(page_number=3, text="  ", width=595.0, height=842.0, truncated=True)

    stored = page.to_document_page(document_id, page_id)

    assert stored.page_number == 3
    assert stored.character_count == 2
    assert stored.metadata == {
        "has_text": False,
        "width": 595.0,
        "height": 842.0,
        "rotation": 0,
        "truncated": True,
    }
    broken = ExtractedPage(page_number=1, text="", width=0, height=0, error="RuntimeError")
    assert (
        broken.to_document_page(document_id, page_id).metadata["extraction_error"] == "RuntimeError"
    )


def test_pages_are_ordered_and_gaps_are_refused() -> None:
    second = ExtractedPage(page_number=2, text="b", width=1, height=1)
    first = ExtractedPage(page_number=1, text="a", width=1, height=1)

    assert [p.page_number for p in ordered_pages([second, first])] == [1, 2]
    with pytest.raises(ExtractionFailedError):
        ordered_pages([first, ExtractedPage(page_number=3, text="c", width=1, height=1)])
