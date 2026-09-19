"""Request correlation and access logging (SPECIFICATIONS.md §36, §50).

For every HTTP request the middleware resolves a request ID (a well-formed incoming header value
is honoured so upstream proxies can correlate; anything else is replaced by a fresh UUID), exposes
it as ``request.state.request_id``, returns it in the response header, binds it to the logging
context for the duration of the request, and emits one access-log entry with the outcome.

It must be the outermost middleware so that every other middleware and handler runs with the
request ID bound; ``create_app`` therefore adds it last.
"""

import re
import time
import uuid

import structlog
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")

logger: structlog.stdlib.BoundLogger = structlog.get_logger("doculens_api.access")


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp, *, header_name: str) -> None:
        self._app = app
        self._header_name = header_name

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        request_id = self._resolve_request_id(scope)
        scope.setdefault("state", {})["request_id"] = request_id
        status_code = 500
        started = time.perf_counter()

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = MutableHeaders(scope=message)
                if self._header_name not in headers:
                    headers.append(self._header_name, request_id)
            await send(message)

        # Scoped binding: context bound by the host (for example a Lambda adapter) is preserved
        # and restored; only ``request_id`` is added for the lifetime of this request.
        with structlog.contextvars.bound_contextvars(request_id=request_id):
            try:
                await self._app(scope, receive, send_with_request_id)
            finally:
                duration_ms = round((time.perf_counter() - started) * 1000, 2)
                logger.info(
                    "request completed",
                    operation="http.request",
                    method=scope["method"],
                    path=scope["path"],
                    status_code=status_code,
                    duration_ms=duration_ms,
                )

    def _resolve_request_id(self, scope: Scope) -> str:
        wanted = self._header_name.lower().encode("latin-1")
        for name, value in scope["headers"]:
            if name.lower() == wanted:
                candidate: str = value.decode("latin-1", errors="replace")
                if REQUEST_ID_PATTERN.match(candidate):
                    return candidate
                break
        return str(uuid.uuid4())
