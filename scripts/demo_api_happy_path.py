"""Local API happy-path demo for portfolio reviewers (no AWS required).

Exercises the vertical slice: register → upload PDF → wait READY → ask with
citations → cleanup. Requires a running API and document worker against the
local compose stack (see docs/development.md).

Usage (from repository root, after ``make infra-up``, ``make db-upgrade``,
API on :8000, worker consuming Redis):

    uv run python scripts/demo_api_happy_path.py
    uv run python scripts/demo_api_happy_path.py --api-url http://127.0.0.1:8000
"""

from __future__ import annotations

import argparse
import secrets
import sys
import time
from http import HTTPStatus
from typing import Any, cast

import httpx

from doculens.testing.pdfs import pdf_with_pages

DEFAULT_API = "http://127.0.0.1:8000"
HANDBOOK_PAGES = (
    "Annual leave is twenty-five days per year.",
    "Parental leave is sixteen weeks at full pay.",
)
QUESTION = "How many days of annual leave do employees get?"
PASSWORD = "correct horse battery staple"  # noqa: S105 — fixture only
READY = "READY"
FAILED = frozenset({"FAILED"})
OK_CREATE: frozenset[HTTPStatus] = frozenset({HTTPStatus.CREATED, HTTPStatus.OK})


def _log(message: str) -> None:
    sys.stdout.write(f"{message}\n")
    sys.stdout.flush()


def _fail(message: str) -> None:
    sys.stderr.write(f"ERROR: {message}\n")
    raise SystemExit(1)


def _expect(
    response: httpx.Response, allowed: frozenset[HTTPStatus] | set[HTTPStatus], *, label: str
) -> dict[str, Any]:
    if response.status_code not in allowed:
        _fail(f"{label} → {response.status_code}: {response.text}")
    if response.status_code == HTTPStatus.NO_CONTENT:
        return {}
    body: object = response.json()
    if not isinstance(body, dict):
        _fail(f"{label} unexpected body: {body!r}")
    return cast("dict[str, Any]", body)


def _register_and_login(client: httpx.Client, email: str) -> dict[str, str]:
    _log(f"1. Register {email}")
    _expect(
        client.post("/api/v1/auth/register", json={"email": email, "password": PASSWORD}),
        OK_CREATE,
        label="register",
    )
    tokens = _expect(
        client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD}),
        {HTTPStatus.OK},
        label="login",
    )
    access = tokens.get("access_token")
    if not isinstance(access, str) or not access:
        _fail("login missing access_token")
    return {"Authorization": f"Bearer {access}"}


def _upload(client: httpx.Client, headers: dict[str, str]) -> str:
    _log("2. Upload handbook PDF")
    pdf = pdf_with_pages(HANDBOOK_PAGES)
    body = _expect(
        client.post(
            "/api/v1/documents",
            headers=headers,
            files={"file": ("handbook-demo.pdf", pdf, "application/pdf")},
        ),
        OK_CREATE,
        label="upload",
    )
    document_id = body.get("id")
    if not isinstance(document_id, str) or not document_id:
        _fail(f"upload missing id: {body}")
    _log(f"   document_id={document_id}")
    return str(document_id)


def _wait_ready(
    client: httpx.Client, headers: dict[str, str], document_id: str, *, timeout: float, poll: float
) -> None:
    _log("3. Wait for READY (worker must be running with QUEUE_BACKEND=redis)")
    deadline = time.monotonic() + timeout
    status = "UPLOADED"
    while time.monotonic() < deadline:
        body = _expect(
            client.get(f"/api/v1/documents/{document_id}", headers=headers),
            {HTTPStatus.OK},
            label="get document",
        )
        status = str(body.get("processing_status", ""))
        if status == READY:
            chunks = body.get("chunk_count")
            indexed = body.get("indexed_at")
            _log(f"   READY chunk_count={chunks} indexed_at={indexed}")
            return
        if status in FAILED:
            _fail(f"processing FAILED: {body.get('processing_error')}")
        time.sleep(poll)
    _fail(f"timed out waiting for READY (last status={status})")


def _ask_with_citations(client: httpx.Client, headers: dict[str, str], document_id: str) -> str:
    _log("4. Create conversation and ask")
    convo = _expect(
        client.post("/api/v1/conversations", headers=headers, json={}),
        OK_CREATE,
        label="create conversation",
    )
    conversation_id = convo.get("id")
    if not isinstance(conversation_id, str) or not conversation_id:
        _fail(f"conversation missing id: {convo}")
    answer = _expect(
        client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            headers=headers,
            json={"question": QUESTION, "document_ids": [document_id]},
        ),
        {HTTPStatus.OK},
        label="ask",
    )
    assistant_raw = answer.get("assistant_message")
    if not isinstance(assistant_raw, dict):
        _fail(f"missing assistant_message: {answer}")
    assistant = cast("dict[str, Any]", assistant_raw)
    content = str(assistant.get("content") or "")
    citations = assistant.get("citations") or []
    _log(f"   answer: {content[:240]!r}")
    _log(f"   citations: {len(citations) if isinstance(citations, list) else 0}")
    if not isinstance(citations, list) or not citations:
        _fail("expected at least one citation")
    matching = [
        c
        for c in citations
        if isinstance(c, dict)
        and isinstance(c.get("quoted_text"), str)
        and "twenty-five" in str(c["quoted_text"]).lower()
    ]
    if not matching and "twenty-five" not in content.lower():
        _fail("expected annual-leave evidence in answer or citation quote")
    return str(conversation_id)


def _cleanup(
    client: httpx.Client,
    headers: dict[str, str],
    *,
    conversation_id: str | None,
    document_id: str | None,
) -> None:
    _log("5. Cleanup")
    if conversation_id:
        client.delete(f"/api/v1/conversations/{conversation_id}", headers=headers)
    if document_id:
        client.delete(f"/api/v1/documents/{document_id}", headers=headers)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default=DEFAULT_API)
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    args = parser.parse_args(argv)
    base = args.api_url.rstrip("/")
    email = f"demo-{secrets.token_hex(4)}@example.com"
    document_id: str | None = None
    conversation_id: str | None = None

    with httpx.Client(base_url=base, timeout=args.timeout) as client:
        if client.get("/health/live").status_code != HTTPStatus.OK:
            _fail("/health/live failed (is the API running?)")
        ready = client.get("/health/ready")
        if ready.status_code != HTTPStatus.OK:
            _fail(f"/health/ready → {ready.status_code}: {ready.text}")

        headers = _register_and_login(client, email)
        document_id = _upload(client, headers)
        _wait_ready(client, headers, document_id, timeout=args.timeout, poll=args.poll_seconds)
        conversation_id = _ask_with_citations(client, headers, document_id)
        _cleanup(client, headers, conversation_id=conversation_id, document_id=document_id)

    _log("PASS — auth, async ingest, hybrid RAG, and citations exercised.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
