"""DocuLens HTTP API: the FastAPI interface layer over ``doculens`` (SPECIFICATIONS.md §72).

Routers, request/response schemas, middleware and dependency wiring live here. Business rules do
not: they belong to ``doculens.application`` and ``doculens.domain``.
"""

from importlib.metadata import version

__version__ = version("doculens-api")
