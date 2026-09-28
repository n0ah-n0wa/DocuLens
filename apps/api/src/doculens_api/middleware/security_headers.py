"""Baseline security response headers (SPECIFICATIONS.md §53).

The API serves JSON to a separate frontend origin, so responses must never be sniffed as another
content type, framed, cached by intermediaries, or leak referrers. Headers set explicitly by a
handler take precedence. Cross-origin policy (CORS) is configured on the app factory from
``CORS_ORIGINS`` (local defaults when unset; see OQ-19 for production hosting).

Deployed environments also emit HSTS so browsers refuse cleartext after the first HTTPS response.
"""

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Applied on every response. CSP is intentionally locked down: this process only serves JSON
# and OpenAPI HTML when docs are enabled; framing and active content are never needed.
BASE_SECURITY_HEADERS: tuple[tuple[str, str], ...] = (
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "no-referrer"),
    ("Cache-Control", "no-store"),
    ("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()"),
    (
        "Content-Security-Policy",
        "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
    ),
    ("X-Permitted-Cross-Domain-Policies", "none"),
)

HSTS_HEADER = ("Strict-Transport-Security", "max-age=31536000; includeSubDomains")


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp, *, enable_hsts: bool = False) -> None:
        self._app = app
        self._headers = BASE_SECURITY_HEADERS + ((HSTS_HEADER,) if enable_hsts else ())

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        async def send_with_security_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in self._headers:
                    if name not in headers:
                        headers.append(name, value)
            await send(message)

        await self._app(scope, receive, send_with_security_headers)
