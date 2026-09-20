"""PDF parsing adapters and the composition helpers for ingestion settings."""

from doculens.domain.ingestion import UploadLimits
from doculens.infrastructure.config import CoreSettings
from doculens.infrastructure.pdf.pymupdf_extractor import ExtractionLimits, PyMuPdfExtractor

MEBIBYTE = 1024 * 1024


def build_upload_limits(settings: CoreSettings) -> UploadLimits:
    return UploadLimits(
        max_file_size_bytes=settings.max_file_size_mb * MEBIBYTE,
        max_pages_per_document=settings.max_pages_per_document,
        max_documents_per_user=settings.max_documents_per_user,
    )


def build_pdf_extractor(settings: CoreSettings) -> PyMuPdfExtractor:
    memory_limit = settings.pdf_extraction_memory_limit_mb
    return PyMuPdfExtractor(
        ExtractionLimits(
            timeout_seconds=settings.pdf_extraction_timeout_seconds,
            memory_limit_bytes=memory_limit * MEBIBYTE if memory_limit else None,
            max_characters_per_page=settings.pdf_max_characters_per_page,
            max_total_characters=settings.pdf_max_total_characters,
        )
    )


__all__ = [
    "ExtractionLimits",
    "PyMuPdfExtractor",
    "build_pdf_extractor",
    "build_upload_limits",
]
