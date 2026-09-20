"""PDF fixtures for tests, generated on the fly so no binary files live in the repository."""

from collections.abc import Mapping, Sequence
from multiprocessing.connection import Connection

import pymupdf

# A structurally valid PDF whose page tree is empty. PyMuPDF refuses to *write* such a file but
# opens it fine, which is exactly the "empty PDF" case the pipeline must reject (§64).
ZERO_PAGE_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
    b"2 0 obj\n<< /Type /Pages /Kids [] /Count 0 >>\nendobj\n"
    b"xref\n0 3\n0000000000 65535 f \n0000000009 00000 n \n0000000058 00000 n \n"
    b"trailer\n<< /Size 3 /Root 1 0 R >>\nstartxref\n110\n%%EOF\n"
)


def pdf_with_pages(
    texts: Sequence[str | None], *, metadata: Mapping[str, str] | None = None
) -> bytes:
    """One page per entry; ``None`` produces a page without any text (a scanned page)."""
    document = pymupdf.open()
    for text in texts:
        page = document.new_page()
        if text is not None:
            page.insert_text((72, 72), text)
    if metadata:
        document.set_metadata(dict(metadata))
    data: bytes = document.tobytes()
    document.close()
    return data


def encrypted_pdf(text: str = "confidential") -> bytes:
    document = pymupdf.open()
    document.new_page().insert_text((72, 72), text)
    data: bytes = document.tobytes(
        encryption=pymupdf.PDF_ENCRYPT_AES_256,  # type: ignore[attr-defined]  # runtime constant
        user_pw="user-secret",
        owner_pw="owner-secret",
    )
    document.close()
    return data


def corrupted_pdf() -> bytes:
    """Carries the signature (so intake accepts it) but no parsable structure."""
    return b"%PDF-1.7\n" + b"\x00\xff garbage that is not a pdf object " * 64


def slow_runner(*_: object) -> None:
    """A parser stand-in that never answers: exercises the extractor's time limit."""
    import time  # noqa: PLC0415 - child process only

    time.sleep(60)


def crashing_runner(*_: object) -> None:
    """A parser stand-in that dies without a word: exercises the crash path."""
    import os  # noqa: PLC0415 - child process only

    os._exit(3)


def erroring_runner(connection: Connection, *_: object) -> None:
    """A parser stand-in whose failure is reported over the pipe."""
    connection.send_bytes(b'{"kind": "error", "error": "RuntimeError", "detail": "boom"}')
    connection.close()


def oversized_runner(connection: Connection, *_: object) -> None:
    """A parser stand-in that answers with far more than the parent may accept."""
    connection.send_bytes(b'{"kind": "ok", "payload": "' + b"x" * (64 * 1024 * 1024) + b'"}')
    connection.close()


def pickling_runner(connection: Connection, *_: object) -> None:
    """A parser stand-in that sends a pickle instead of JSON, as a compromised child might."""
    connection.send({"kind": "ok", "payload": {}})
    connection.close()


def padded_pdf(data: bytes, *, target_size: int) -> bytes:
    """A valid PDF grown past ``target_size`` with trailing comment lines (still parsable)."""
    line = b"% padding " + b"x" * 70 + b"\n"
    padding = line * (max(0, target_size - len(data)) // len(line) + 1)
    return data + padding
