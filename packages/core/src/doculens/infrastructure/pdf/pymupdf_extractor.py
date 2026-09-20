"""PyMuPDF-based ``PdfExtractor`` that parses in an isolated, bounded child process (§13, §53, §64).

A PDF is attacker-controlled input to a large C library. Every parse therefore runs in a fresh
process with a wall-clock limit and, where the platform allows, an address-space limit. A parser
hang or crash ends that child, never the worker: a timeout becomes ``ExtractionTimeoutError``, a
crash ``ExtractionFailedError``, and the document ends in ``FAILED`` with a safe message.

The isolation boundary is treated as untrusted in both directions:

- the child returns one JSON message (never a pickle, which could execute code in the worker
  if the child were compromised), and the parent refuses a message larger than the configured
  text budget allows;
- page text is sanitised and bounded per page and per document before it is sent, so the
  worker's memory is bounded by configuration whatever the file contains;
- the wall-clock limit covers the whole exchange, including a child that stalls mid-message.
"""

import asyncio
import json
import logging
import multiprocessing
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from multiprocessing.connection import Connection
from typing import Any

from doculens.domain.ingestion import (
    ContentTooLargeError,
    CorruptedPdfError,
    EmptyPdfError,
    EncryptedPdfError,
    ExtractedPage,
    ExtractionFailedError,
    ExtractionResult,
    ExtractionTimeoutError,
    PdfInfo,
    PdfMetadata,
    PdfRejectedError,
    TooManyPagesError,
    sanitize_page_text,
)

if sys.platform != "win32":
    import resource  # POSIX only: address-space limits are not available on Windows

logger = logging.getLogger(__name__)

ChildRunner = Callable[[Connection, str, bytes, int, "ExtractionLimits"], None]

INSPECT = "inspect"
EXTRACT = "extract"
_GRACE_SECONDS = 5.0
_MESSAGE_HEADROOM_BYTES = 4 * 1024 * 1024
_MAX_UTF8_BYTES_PER_CHARACTER = 4
_MAX_WARNING_CHARACTERS = 1000
_MAX_DIAGNOSTIC_CHARACTERS = 500


@dataclass(frozen=True, slots=True)
class ExtractionLimits:
    timeout_seconds: float
    memory_limit_bytes: int | None
    max_characters_per_page: int
    max_total_characters: int

    @property
    def max_message_bytes(self) -> int:
        """The largest child message the parent accepts: the text budget plus structure."""
        return self.max_total_characters * _MAX_UTF8_BYTES_PER_CHARACTER + _MESSAGE_HEADROOM_BYTES


class PyMuPdfExtractor:
    def __init__(self, limits: ExtractionLimits, *, runner: ChildRunner | None = None) -> None:
        self._limits = limits
        self._runner: ChildRunner = runner if runner is not None else run_in_child

    async def inspect(self, data: bytes, *, max_pages: int) -> PdfInfo:
        payload = await asyncio.to_thread(self._run_isolated, INSPECT, data, max_pages)
        return PdfInfo(page_count=int(payload["page_count"]), metadata=_metadata(payload))

    async def extract(self, data: bytes, *, max_pages: int) -> ExtractionResult:
        payload = await asyncio.to_thread(self._run_isolated, EXTRACT, data, max_pages)
        info = PdfInfo(page_count=int(payload["page_count"]), metadata=_metadata(payload))
        pages = tuple(
            ExtractedPage(
                page_number=int(page["page_number"]),
                text=str(page["text"]),
                width=float(page["width"]),
                height=float(page["height"]),
                rotation=int(page["rotation"]),
                truncated=bool(page["truncated"]),
                error=page["error"],
            )
            for page in payload["pages"]
        )
        return ExtractionResult(info=info, pages=pages)

    def _run_isolated(self, mode: str, data: bytes, max_pages: int) -> dict[str, Any]:
        context = multiprocessing.get_context("spawn")
        parent_end, child_end = context.Pipe(duplex=False)
        process = context.Process(
            target=self._runner,
            args=(child_end, mode, data, max_pages, self._limits),
            daemon=True,
            name="doculens-pdf",
        )
        timed_out = threading.Event()

        def expire() -> None:
            timed_out.set()
            process.kill()

        # The timer bounds the whole exchange: spawning, parsing and transferring the result.
        timer = threading.Timer(self._limits.timeout_seconds, expire)
        process.start()
        child_end.close()
        timer.start()
        try:
            raw = parent_end.recv_bytes(maxlength=self._limits.max_message_bytes)
        except (EOFError, OSError) as exc:
            timer.cancel()
            process.join(_GRACE_SECONDS)
            if timed_out.is_set():
                logger.warning(
                    "pdf parsing exceeded its time limit",
                    extra={"operation": "pdf.timeout", "timeout": self._limits.timeout_seconds},
                )
                raise ExtractionTimeoutError(
                    diagnostics=f"no result within {self._limits.timeout_seconds}s"
                ) from exc
            if isinstance(exc, OSError) and not isinstance(exc, EOFError):
                raise ExtractionFailedError(
                    diagnostics="parser result exceeded the message limit"
                ) from exc
            raise ExtractionFailedError(
                diagnostics=f"parser process ended without a result (exit code {process.exitcode})"
            ) from exc
        finally:
            timer.cancel()
            parent_end.close()
            process.join(_GRACE_SECONDS)
            if process.is_alive():
                process.kill()
                process.join(_GRACE_SECONDS)

        message = _decode_message(raw)
        kind = message.get("kind")
        if kind == "ok":
            payload = message.get("payload")
            if not isinstance(payload, dict):
                raise ExtractionFailedError(diagnostics="parser result has no payload")
            self._log_warnings(payload)
            return payload
        if kind == "rejected":
            raise PdfRejectedError.for_code(
                str(message.get("code")), diagnostics=_short(message.get("diagnostics"))
            )
        raise ExtractionFailedError(
            diagnostics=f"{_short(message.get('error'))}: {_short(message.get('detail'))}"
        )

    @staticmethod
    def _log_warnings(payload: dict[str, Any]) -> None:
        warnings = payload.pop("warnings", None)
        if warnings:
            logger.info(
                "pdf parser reported warnings",
                extra={"operation": "pdf.warnings", "warnings": _short(warnings)},
            )


def _decode_message(raw: bytes) -> dict[str, Any]:
    try:
        message = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ExtractionFailedError(diagnostics="parser result is not valid JSON") from exc
    if not isinstance(message, dict):
        raise ExtractionFailedError(diagnostics="parser result has an unexpected shape")
    return message


def _short(value: object) -> str | None:
    if value is None:
        return None
    return str(value)[:_MAX_DIAGNOSTIC_CHARACTERS]


def _metadata(payload: dict[str, Any]) -> PdfMetadata:
    raw = payload.get("metadata")
    return PdfMetadata.from_untrusted(raw if isinstance(raw, dict) else {})


# -- child process ---------------------------------------------------------------------------------


def run_in_child(
    connection: Connection, mode: str, data: bytes, max_pages: int, limits: ExtractionLimits
) -> None:
    """Entry point of the parser process. Sends exactly one JSON message and exits."""
    try:
        _apply_memory_limit(limits.memory_limit_bytes)
        payload = _parse(mode, data, max_pages, limits)
        message: dict[str, Any] = {"kind": "ok", "payload": payload}
    except PdfRejectedError as error:
        message = {"kind": "rejected", "code": error.code, "diagnostics": error.diagnostics}
    except Exception as error:  # noqa: BLE001 - reported to the parent, which maps it
        message = {
            "kind": "error",
            "error": type(error).__name__,
            "detail": str(error)[:_MAX_DIAGNOSTIC_CHARACTERS],
        }
    try:
        connection.send_bytes(json.dumps(message, ensure_ascii=False).encode("utf-8"))
    finally:
        connection.close()


if sys.platform != "win32":

    def _apply_memory_limit(memory_limit_bytes: int | None) -> None:
        """Best effort: a host that forbids the limit must not fail every document."""
        if memory_limit_bytes is None:
            return
        try:
            _soft, hard = resource.getrlimit(resource.RLIMIT_AS)
            limit = (
                memory_limit_bytes
                if hard == resource.RLIM_INFINITY
                else min(memory_limit_bytes, hard)
            )
            resource.setrlimit(resource.RLIMIT_AS, (limit, hard))
        except (ValueError, OSError):
            return

else:

    def _apply_memory_limit(memory_limit_bytes: int | None) -> None:
        """Windows has no address-space limit; the wall-clock limit still applies."""
        del memory_limit_bytes


def _parse(mode: str, data: bytes, max_pages: int, limits: ExtractionLimits) -> dict[str, Any]:
    import pymupdf  # noqa: PLC0415 - imported in the child only

    pymupdf.TOOLS.mupdf_display_errors(False)  # noqa: FBT003 - library API
    pymupdf.TOOLS.mupdf_warnings(reset=True)
    try:
        document = pymupdf.open(stream=data, filetype="pdf")
    except Exception as error:
        detail = f"{type(error).__name__}: {error}"[:_MAX_DIAGNOSTIC_CHARACTERS]
        raise CorruptedPdfError(diagnostics=detail) from error
    with document:
        if document.needs_pass:
            raise EncryptedPdfError
        page_count = int(document.page_count)
        if page_count == 0:
            raise EmptyPdfError
        if page_count > max_pages:
            raise TooManyPagesError(diagnostics=f"{page_count} pages, limit {max_pages}")
        raw = document.metadata or {}
        metadata = {
            "title": raw.get("title"),
            "author": raw.get("author"),
            "subject": raw.get("subject"),
            "keywords": raw.get("keywords"),
            "creator": raw.get("creator"),
            "producer": raw.get("producer"),
            "created": raw.get("creationDate"),
            "modified": raw.get("modDate"),
            "pdf_version": raw.get("format"),
        }
        payload: dict[str, Any] = {"page_count": page_count, "metadata": metadata}
        if mode == INSPECT:
            return payload
        pages: list[dict[str, Any]] = []
        total_characters = 0
        for index in range(page_count):
            page = _extract_page(document, index, limits)
            total_characters += len(page["text"])
            if total_characters > limits.max_total_characters:
                raise ContentTooLargeError(
                    diagnostics=(
                        f"more than {limits.max_total_characters} characters by page {index + 1}"
                    )
                )
            pages.append(page)
        payload["pages"] = pages
    warnings = pymupdf.TOOLS.mupdf_warnings()
    if warnings:
        payload["warnings"] = warnings[:_MAX_WARNING_CHARACTERS]
    return payload


def _extract_page(document: Any, index: int, limits: ExtractionLimits) -> dict[str, Any]:  # noqa: ANN401 - pymupdf.Document
    number = index + 1
    try:
        page = document[index]
        rect = page.rect
        text, truncated = sanitize_page_text(
            page.get_text("text", sort=True), max_characters=limits.max_characters_per_page
        )
        return {
            "page_number": number,
            "text": text,
            "width": float(rect.width),
            "height": float(rect.height),
            "rotation": int(page.rotation),
            "truncated": truncated,
            "error": None,
        }
    except Exception as error:  # noqa: BLE001 - a broken page is recorded, never dropped (§13)
        return {
            "page_number": number,
            "text": "",
            "width": 0.0,
            "height": 0.0,
            "rotation": 0,
            "truncated": False,
            "error": f"{type(error).__name__}"[:100],
        }
