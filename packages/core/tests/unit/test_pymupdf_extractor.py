"""The isolated PyMuPDF extractor: real parsing in a child process, bounded and fail-safe."""

import time

import pytest

from doculens.domain.ingestion import (
    ContentTooLargeError,
    CorruptedPdfError,
    EmptyPdfError,
    EncryptedPdfError,
    ExtractionFailedError,
    ExtractionTimeoutError,
    TooManyPagesError,
)
from doculens.infrastructure.pdf import ExtractionLimits, PyMuPdfExtractor
from doculens.testing.pdfs import (
    ZERO_PAGE_PDF,
    corrupted_pdf,
    crashing_runner,
    encrypted_pdf,
    erroring_runner,
    oversized_runner,
    pdf_with_pages,
    pickling_runner,
    slow_runner,
)

pytestmark = pytest.mark.unit

LIMITS = ExtractionLimits(
    timeout_seconds=60.0,
    memory_limit_bytes=None,
    max_characters_per_page=100_000,
    max_total_characters=1_000_000,
)


@pytest.fixture(scope="module")
def extractor() -> PyMuPdfExtractor:
    return PyMuPdfExtractor(LIMITS)


@pytest.fixture(scope="module")
def sample() -> bytes:
    return pdf_with_pages(
        ["First page text", None, "Third page\nwith two lines"],
        metadata={"title": "Quarterly report", "author": "Ada", "subject": "Q3"},
    )


async def test_inspect_reports_pages_and_sanitised_metadata_without_text(
    extractor: PyMuPdfExtractor, sample: bytes
) -> None:
    info = await extractor.inspect(sample, max_pages=10)

    assert info.page_count == 3
    assert info.metadata.title == "Quarterly report"
    assert info.metadata.author == "Ada"
    assert info.metadata.subject == "Q3"
    assert info.metadata.pdf_version is not None
    assert info.metadata.pdf_version.startswith("PDF")


async def test_extract_preserves_page_order_boundaries_and_empty_pages(
    extractor: PyMuPdfExtractor, sample: bytes
) -> None:
    result = await extractor.extract(sample, max_pages=10)

    assert [page.page_number for page in result.pages] == [1, 2, 3]
    assert "First page text" in result.pages[0].text
    assert result.pages[1].text == ""
    assert result.pages[1].has_text is False
    assert "Third page" in result.pages[2].text
    assert "two lines" in result.pages[2].text
    assert result.empty_page_count == 1
    assert result.text_page_count == 2
    assert all(page.width > 0 and page.height > 0 for page in result.pages)
    assert not any(page.truncated for page in result.pages)
    assert result.info.page_count == 3


async def test_page_text_is_capped_per_page(sample: bytes) -> None:
    extractor = PyMuPdfExtractor(
        ExtractionLimits(
            timeout_seconds=60.0,
            memory_limit_bytes=None,
            max_characters_per_page=5,
            max_total_characters=1_000_000,
        )
    )

    result = await extractor.extract(sample, max_pages=10)

    assert result.pages[0].text == "First"
    assert result.pages[0].truncated is True


@pytest.mark.parametrize(
    ("data", "error"),
    [
        (corrupted_pdf(), CorruptedPdfError),
        (encrypted_pdf(), EncryptedPdfError),
        (ZERO_PAGE_PDF, EmptyPdfError),
    ],
)
async def test_unusable_files_are_rejected_with_a_safe_message(
    extractor: PyMuPdfExtractor, data: bytes, error: type[Exception]
) -> None:
    with pytest.raises(error) as inspected:
        await extractor.inspect(data, max_pages=10)
    with pytest.raises(error) as extracted:
        await extractor.extract(data, max_pages=10)

    for excinfo in (inspected, extracted):
        assert excinfo.value.code == error.code  # type: ignore[attr-defined]
        assert "Error" not in excinfo.value.message  # type: ignore[attr-defined]


async def test_the_page_limit_is_enforced_inside_the_parser(
    extractor: PyMuPdfExtractor, sample: bytes
) -> None:
    with pytest.raises(TooManyPagesError) as excinfo:
        await extractor.inspect(sample, max_pages=2)

    assert excinfo.value.diagnostics == "3 pages, limit 2"


async def test_a_hanging_parser_is_killed_at_the_time_limit(sample: bytes) -> None:
    extractor = PyMuPdfExtractor(
        ExtractionLimits(
            timeout_seconds=1.0,
            memory_limit_bytes=None,
            max_characters_per_page=10,
            max_total_characters=1_000_000,
        ),
        runner=slow_runner,
    )
    started = time.monotonic()

    with pytest.raises(ExtractionTimeoutError):
        await extractor.extract(sample, max_pages=10)

    assert time.monotonic() - started < 30  # the child did not run its full 60 s sleep


async def test_a_crashing_parser_becomes_a_failed_extraction(sample: bytes) -> None:
    extractor = PyMuPdfExtractor(LIMITS, runner=crashing_runner)

    with pytest.raises(ExtractionFailedError) as excinfo:
        await extractor.inspect(sample, max_pages=10)

    assert "exit code" in (excinfo.value.diagnostics or "")


async def test_parser_errors_are_reported_without_leaking_into_the_message(sample: bytes) -> None:
    extractor = PyMuPdfExtractor(LIMITS, runner=erroring_runner)

    with pytest.raises(ExtractionFailedError) as excinfo:
        await extractor.extract(sample, max_pages=10)

    assert excinfo.value.diagnostics == "RuntimeError: boom"
    assert "boom" not in excinfo.value.message


async def test_the_document_wide_text_budget_is_enforced_in_the_parser(sample: bytes) -> None:
    extractor = PyMuPdfExtractor(
        ExtractionLimits(
            timeout_seconds=60.0,
            memory_limit_bytes=None,
            max_characters_per_page=100_000,
            max_total_characters=1_000,
        )
    )
    await extractor.extract(sample, max_pages=10)  # well within budget

    tight = PyMuPdfExtractor(
        ExtractionLimits(
            timeout_seconds=60.0,
            memory_limit_bytes=None,
            max_characters_per_page=1_000,
            max_total_characters=1_000,
        )
    )
    line = "x " * 30
    huge = pdf_with_pages(["\n".join([line] * 14)] * 3)  # ~840 characters per page
    with pytest.raises(ContentTooLargeError) as excinfo:
        await tight.extract(huge, max_pages=10)
    assert "by page 2" in (excinfo.value.diagnostics or "")


async def test_a_parser_answer_over_the_byte_limit_is_refused_unread(sample: bytes) -> None:
    extractor = PyMuPdfExtractor(
        ExtractionLimits(
            timeout_seconds=60.0,
            memory_limit_bytes=None,
            max_characters_per_page=1_000,
            max_total_characters=1_000,
        ),
        runner=oversized_runner,
    )

    with pytest.raises(ExtractionFailedError) as excinfo:
        await extractor.inspect(sample, max_pages=10)

    assert "message limit" in (excinfo.value.diagnostics or "")


async def test_a_parser_answer_that_is_not_json_is_refused(sample: bytes) -> None:
    """A compromised child cannot smuggle a pickle into the worker."""
    extractor = PyMuPdfExtractor(LIMITS, runner=pickling_runner)

    with pytest.raises(ExtractionFailedError) as excinfo:
        await extractor.inspect(sample, max_pages=10)

    assert "not valid JSON" in (excinfo.value.diagnostics or "")
