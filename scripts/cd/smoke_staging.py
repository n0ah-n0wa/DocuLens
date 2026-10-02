"""Deployed-environment smoke tests against a live DocuLens stack (§59-§60).

Used by staging and production CD. Verifies, with controlled fixture data created
by this run only:

1. frontend availability
2. API availability (liveness + readiness)
3. authentication (register + login)
4. document upload
5. document processing through READY
6. vector indexing (chunk_count + indexed_at)
7. RAG query
8. citation response
9. deletion (document, conversation, collection) with confirmation

Cleans up every resource it creates, including on failure. Does not depend on
manually prepared environment state.

Environment:
  API_URL, FRONTEND_URL - required (or loadable from DOCULENS_DEPLOY_ENV_FILE /
    .local/staging-deploy.env / .local/production-deploy.env)
  SMOKE_EMAIL_PREFIX - default staging-smoke (production CD sets production-smoke)
  SMOKE_LABEL - log label, default Staging
  SMOKE_READY_TIMEOUT_SECONDS - default 300
  SMOKE_POLL_INTERVAL_SECONDS - default 5
  SMOKE_HTTP_TIMEOUT_SECONDS - default 60 (ask may take longer; ask uses 180)
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import time
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from typing import NoReturn
from urllib.parse import urljoin

import httpx

from doculens.testing.pdfs import pdf_with_pages

ROOT = Path(__file__).resolve().parents[2]


def _deploy_env_file() -> Path:
    override = os.environ.get("DOCULENS_DEPLOY_ENV_FILE", "").strip()
    if override:
        return Path(override)
    production = ROOT / ".local" / "production-deploy.env"
    staging = ROOT / ".local" / "staging-deploy.env"
    if production.is_file() and not staging.is_file():
        return production
    return staging


DEPLOY_ENV_FILE = ROOT / ".local" / "staging-deploy.env"  # legacy alias; prefer _deploy_env_file()

# Controlled fixture - same handbook text as the critical Playwright suite.
HANDBOOK_PAGES: tuple[str, ...] = (
    "Annual leave is twenty-five days per year.",
    "Parental leave is sixteen weeks at full pay.",
)
HANDBOOK_FILENAME = "handbook-smoke.pdf"
RAG_QUESTION = "How many days of annual leave do employees get?"
# Distinctive phrase that must appear in retrieved citation text (document evidence).
CITATION_FRAGMENT = "twenty-five"
SMOKE_PASSWORD = "correct horse battery staple"  # noqa: S105 - fixture only, never a real secret
FAILED_STATUSES = frozenset({"FAILED"})
READY_STATUS = "READY"


@dataclass
class SmokeContext:
    api_url: str
    frontend_url: str
    client: httpx.Client
    email: str = ""
    password: str = SMOKE_PASSWORD
    access_token: str | None = None
    refresh_token: str | None = None
    collection_id: str | None = None
    document_id: str | None = None
    conversation_id: str | None = None

    def auth_headers(self) -> dict[str, str]:
        if not self.access_token:
            message = "not authenticated"
            raise RuntimeError(message)
        return {"Authorization": f"Bearer {self.access_token}"}


def _log(message: str) -> None:
    sys.stdout.write(f"{message}\n")
    sys.stdout.flush()


def _fail(message: str) -> NoReturn:
    raise AssertionError(message)


def _load_deploy_env() -> None:
    env_file = _deploy_env_file()
    if not env_file.is_file():
        return
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def _require_urls() -> tuple[str, str]:
    _load_deploy_env()
    api = os.environ.get("API_URL", "").rstrip("/")
    frontend = os.environ.get("FRONTEND_URL", "").rstrip("/")
    if not api:
        _fail("API_URL is required (or run deploy_staging.sh / deploy_production.sh first)")
    if not frontend:
        _fail("FRONTEND_URL is required (or run deploy_staging.sh / deploy_production.sh first)")
    return api, frontend


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return float(raw)


def _smoke_label() -> str:
    return os.environ.get("SMOKE_LABEL", "Staging").strip() or "Staging"


def _unique_email() -> str:
    prefix = os.environ.get("SMOKE_EMAIL_PREFIX", "staging-smoke").strip() or "staging-smoke"
    suffix = secrets.token_hex(4)
    return f"{prefix}-{int(time.time())}-{suffix}@example.com"


def _json_body(response: httpx.Response) -> object:
    try:
        return response.json()
    except json.JSONDecodeError:
        return response.text


def _expect_status(response: httpx.Response, *codes: int, label: str) -> object:
    if response.status_code not in codes:
        _fail(f"{label}: expected HTTP {codes}, got {response.status_code}: {_json_body(response)}")
    if response.status_code == HTTPStatus.NO_CONTENT or not response.content:
        return None
    return _json_body(response)


def check_frontend(ctx: SmokeContext) -> None:
    _log(f"==> frontend availability ({ctx.frontend_url})")
    home = ctx.client.get(ctx.frontend_url + "/")
    if home.status_code != HTTPStatus.OK:
        _fail(f"frontend /: HTTP {home.status_code}")
    if "doculens" not in home.text.lower():
        _fail("frontend homepage does not contain DocuLens")
    login = ctx.client.get(urljoin(ctx.frontend_url + "/", "login"))
    if login.status_code != HTTPStatus.OK:
        _fail(f"frontend /login: HTTP {login.status_code}")
    login_lower = login.text.lower()
    if "sign in" not in login_lower and "login" not in login_lower:
        _fail("frontend /login does not look like a sign-in page")
    _log("OK  frontend")


def check_api_availability(ctx: SmokeContext) -> None:
    _log(f"==> API availability ({ctx.api_url})")
    live = ctx.client.get(f"{ctx.api_url}/health/live")
    body = _expect_status(live, HTTPStatus.OK, label="GET /health/live")
    if not isinstance(body, dict):
        _fail(f"/health/live returned non-object: {body!r}")
    if body.get("status") != "ok" or body.get("service") != "doculens-api":
        _fail(f"/health/live unexpected body: {body}")
    ready = ctx.client.get(f"{ctx.api_url}/health/ready")
    ready_body = _expect_status(ready, HTTPStatus.OK, label="GET /health/ready")
    if not isinstance(ready_body, dict) or ready_body.get("status") != "ready":
        _fail(
            f"API not ready (vector store / dependencies must be healthy for smoke): {ready_body}"
        )
    _log("OK  API live + ready")


def check_authentication(ctx: SmokeContext) -> None:
    _log("==> authentication")
    ctx.email = _unique_email()
    registered = ctx.client.post(
        f"{ctx.api_url}/api/v1/auth/register",
        json={"email": ctx.email, "password": ctx.password},
    )
    user = _expect_status(registered, HTTPStatus.CREATED, label="POST /auth/register")
    if not isinstance(user, dict) or user.get("email") != ctx.email:
        _fail(f"register returned unexpected user: {user}")

    logged_in = ctx.client.post(
        f"{ctx.api_url}/api/v1/auth/login",
        json={"email": ctx.email, "password": ctx.password},
    )
    tokens = _expect_status(logged_in, HTTPStatus.OK, label="POST /auth/login")
    if not isinstance(tokens, dict):
        _fail(f"login returned non-object: {tokens!r}")
    access = tokens.get("access_token")
    refresh = tokens.get("refresh_token")
    if not isinstance(access, str) or not access:
        _fail("login missing access_token")
    if not isinstance(refresh, str) or not refresh:
        _fail("login missing refresh_token")
    if tokens.get("token_type") != "Bearer":
        _fail(f"unexpected token_type: {tokens.get('token_type')!r}")
    ctx.access_token = access
    ctx.refresh_token = refresh

    me = ctx.client.get(f"{ctx.api_url}/api/v1/users/me", headers=ctx.auth_headers())
    profile = _expect_status(me, HTTPStatus.OK, label="GET /users/me")
    if not isinstance(profile, dict) or profile.get("email") != ctx.email:
        _fail(f"/users/me unexpected: {profile}")
    _log(f"OK  auth as {ctx.email}")


def check_upload(ctx: SmokeContext) -> None:
    _log("==> document upload")
    collection = ctx.client.post(
        f"{ctx.api_url}/api/v1/collections",
        headers=ctx.auth_headers(),
        json={
            "name": f"{_smoke_label()} smoke {int(time.time())}",
            "description": "Ephemeral smoke collection - safe to delete",
        },
    )
    created = _expect_status(collection, HTTPStatus.CREATED, label="POST /collections")
    if not isinstance(created, dict) or not created.get("id"):
        _fail(f"collection create unexpected: {created}")
    ctx.collection_id = str(created["id"])

    pdf_bytes = pdf_with_pages(HANDBOOK_PAGES)
    upload = ctx.client.post(
        f"{ctx.api_url}/api/v1/documents",
        headers=ctx.auth_headers(),
        data={"collection_id": ctx.collection_id},
        files={"file": (HANDBOOK_FILENAME, pdf_bytes, "application/pdf")},
    )
    document = _expect_status(upload, HTTPStatus.CREATED, label="POST /documents")
    if not isinstance(document, dict):
        _fail(f"upload returned non-object: {document!r}")
    document_id = document.get("id")
    if not document_id:
        _fail(f"upload missing id: {document}")
    ctx.document_id = str(document_id)
    if document.get("filename") != HANDBOOK_FILENAME:
        _fail(f"unexpected filename: {document.get('filename')!r}")
    if document.get("collection_id") != ctx.collection_id:
        _fail(f"document not attached to collection: {document}")
    if document.get("processing_status") not in {
        "UPLOADED",
        "VALIDATING",
        "EXTRACTING",
        "CHUNKING",
        "EMBEDDING",
        "INDEXING",
        READY_STATUS,
    }:
        _fail(f"unexpected initial status: {document.get('processing_status')!r}")
    _log(f"OK  uploaded document {ctx.document_id}")


def check_processing_and_indexing(ctx: SmokeContext) -> None:
    _log("==> document processing + vector indexing")
    if not ctx.document_id:
        _fail("no document_id")
    timeout = _env_float("SMOKE_READY_TIMEOUT_SECONDS", 300.0)
    interval = _env_float("SMOKE_POLL_INTERVAL_SECONDS", 5.0)
    deadline = time.monotonic() + timeout
    last: dict[str, object] | None = None
    while time.monotonic() < deadline:
        response = ctx.client.get(
            f"{ctx.api_url}/api/v1/documents/{ctx.document_id}",
            headers=ctx.auth_headers(),
        )
        body = _expect_status(response, HTTPStatus.OK, label="GET /documents/{id}")
        if not isinstance(body, dict):
            _fail(f"document poll non-object: {body!r}")
        last = body
        status = body.get("processing_status")
        _log(f"    status={status} chunk_count={body.get('chunk_count')}")
        if status in FAILED_STATUSES:
            _fail(f"document processing FAILED: {body.get('processing_error')!r}")
        if status == READY_STATUS:
            break
        time.sleep(interval)
    else:
        _fail(f"document not READY within {timeout}s; last={last}")

    ready_doc = last
    if ready_doc is None:
        _fail("document poll completed without a body")
    chunk_count = ready_doc.get("chunk_count")
    if not isinstance(chunk_count, int) or chunk_count < 1:
        _fail(f"READY document must have chunk_count >= 1 (indexing): {ready_doc}")
    indexed_at = ready_doc.get("indexed_at")
    if not indexed_at:
        _fail(f"READY document missing indexed_at: {ready_doc}")
    _log(f"OK  READY with {chunk_count} chunks (indexed_at={indexed_at})")


def check_rag_and_citations(ctx: SmokeContext) -> None:
    _log("==> RAG query + citation response")
    if not ctx.document_id or not ctx.collection_id:
        _fail("missing document or collection")
    created = ctx.client.post(
        f"{ctx.api_url}/api/v1/conversations",
        headers=ctx.auth_headers(),
        json={"title": f"{_smoke_label()} smoke RAG", "collection_id": ctx.collection_id},
    )
    conversation = _expect_status(created, HTTPStatus.CREATED, label="POST /conversations")
    if not isinstance(conversation, dict) or not conversation.get("id"):
        _fail(f"conversation create unexpected: {conversation}")
    ctx.conversation_id = str(conversation["id"])

    ask_timeout = _env_float("SMOKE_ASK_TIMEOUT_SECONDS", 180.0)
    asked = ctx.client.post(
        f"{ctx.api_url}/api/v1/conversations/{ctx.conversation_id}/messages",
        headers=ctx.auth_headers(),
        json={"question": RAG_QUESTION, "document_ids": [ctx.document_id]},
        timeout=ask_timeout,
    )
    answer = _expect_status(asked, HTTPStatus.CREATED, label="POST .../messages")
    if not isinstance(answer, dict):
        _fail(f"ask returned non-object: {answer!r}")
    if answer.get("outcome") != "answered":
        _fail(f"expected outcome=answered, got {answer.get('outcome')!r}: {answer}")
    assistant = answer.get("assistant_message")
    if not isinstance(assistant, dict):
        _fail(f"missing assistant_message: {answer}")
    content = assistant.get("content")
    if not isinstance(content, str) or not content.strip():
        _fail(f"empty assistant content: {assistant}")
    citations = assistant.get("citations")
    if not isinstance(citations, list) or not citations:
        _fail(f"expected at least one citation: {assistant}")
    matching = [
        c
        for c in citations
        if isinstance(c, dict)
        and c.get("document_id") == ctx.document_id
        and isinstance(c.get("quoted_text"), str)
        and CITATION_FRAGMENT in str(c["quoted_text"]).lower()
        and isinstance(c.get("page_number"), int)
        and int(c["page_number"]) >= 1
    ]
    if not matching:
        _fail(
            f"no citation quotes controlled evidence {CITATION_FRAGMENT!r} "
            f"from document {ctx.document_id}: {citations}"
        )
    first = matching[0]
    page_number = first["page_number"]
    retrieval = answer.get("retrieval")
    if isinstance(retrieval, dict) and retrieval.get("documents_in_scope", 0) < 1:
        _fail(f"retrieval documents_in_scope < 1: {retrieval}")
    _log(f"OK  RAG answered with {len(citations)} citation(s); matched page={page_number}")


def check_deletion(ctx: SmokeContext) -> None:
    _log("==> deletion")
    if ctx.document_id:
        deleted = ctx.client.delete(
            f"{ctx.api_url}/api/v1/documents/{ctx.document_id}",
            headers=ctx.auth_headers(),
        )
        _expect_status(deleted, HTTPStatus.NO_CONTENT, label="DELETE /documents/{id}")
        gone = ctx.client.get(
            f"{ctx.api_url}/api/v1/documents/{ctx.document_id}",
            headers=ctx.auth_headers(),
        )
        if gone.status_code != HTTPStatus.NOT_FOUND:
            _fail(f"document still resolvable after delete: HTTP {gone.status_code}")
        ctx.document_id = None
        _log("OK  document deleted")

    if ctx.conversation_id:
        deleted_c = ctx.client.delete(
            f"{ctx.api_url}/api/v1/conversations/{ctx.conversation_id}",
            headers=ctx.auth_headers(),
        )
        _expect_status(deleted_c, HTTPStatus.NO_CONTENT, label="DELETE /conversations/{id}")
        ctx.conversation_id = None
        _log("OK  conversation deleted")

    if ctx.collection_id:
        deleted_col = ctx.client.delete(
            f"{ctx.api_url}/api/v1/collections/{ctx.collection_id}",
            headers=ctx.auth_headers(),
        )
        _expect_status(deleted_col, HTTPStatus.NO_CONTENT, label="DELETE /collections/{id}")
        ctx.collection_id = None
        _log("OK  collection deleted")


def cleanup(ctx: SmokeContext) -> None:
    """Best-effort teardown so a mid-run failure does not leave environment clutter."""
    _log("==> cleanup")
    headers = {}
    if ctx.access_token:
        headers = {"Authorization": f"Bearer {ctx.access_token}"}

    if ctx.document_id and headers:
        try:
            ctx.client.delete(
                f"{ctx.api_url}/api/v1/documents/{ctx.document_id}",
                headers=headers,
            )
        except httpx.HTTPError as exc:
            _log(f"cleanup document skipped: {exc}")
        ctx.document_id = None

    if ctx.conversation_id and headers:
        try:
            ctx.client.delete(
                f"{ctx.api_url}/api/v1/conversations/{ctx.conversation_id}",
                headers=headers,
            )
        except httpx.HTTPError as exc:
            _log(f"cleanup conversation skipped: {exc}")
        ctx.conversation_id = None

    if ctx.collection_id and headers:
        try:
            ctx.client.delete(
                f"{ctx.api_url}/api/v1/collections/{ctx.collection_id}",
                headers=headers,
            )
        except httpx.HTTPError as exc:
            _log(f"cleanup collection skipped: {exc}")
        ctx.collection_id = None

    if ctx.refresh_token:
        try:
            ctx.client.post(
                f"{ctx.api_url}/api/v1/auth/logout",
                json={"refresh_token": ctx.refresh_token},
            )
        except httpx.HTTPError as exc:
            _log(f"cleanup logout skipped: {exc}")
        ctx.refresh_token = None
        ctx.access_token = None
    _log("OK  cleanup finished")


def run() -> int:
    api_url, frontend_url = _require_urls()
    http_timeout = _env_float("SMOKE_HTTP_TIMEOUT_SECONDS", 60.0)
    with httpx.Client(timeout=http_timeout, follow_redirects=True) as client:
        ctx = SmokeContext(api_url=api_url, frontend_url=frontend_url, client=client)
        try:
            check_frontend(ctx)
            check_api_availability(ctx)
            check_authentication(ctx)
            check_upload(ctx)
            check_processing_and_indexing(ctx)
            check_rag_and_citations(ctx)
            check_deletion(ctx)
        except AssertionError as exc:
            sys.stderr.write(f"FAIL  {exc}\n")
            return 1
        except httpx.HTTPError as exc:
            sys.stderr.write(f"FAIL  HTTP error: {exc}\n")
            return 1
        finally:
            cleanup(ctx)
    _log(f"==> {_smoke_label()} smoke checks passed")
    return 0


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
