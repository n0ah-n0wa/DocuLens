"""ASGI middleware. Implemented as pure ASGI callables so streaming responses and context variables
behave correctly (Starlette's ``BaseHTTPMiddleware`` is avoided on purpose)."""
