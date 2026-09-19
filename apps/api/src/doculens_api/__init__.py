"""DocuLens HTTP API: the FastAPI interface layer over ``doculens`` (SPECIFICATIONS.md §72).

Routers, request/response schemas, middleware and dependency wiring live here. Business rules do
not: they belong to ``doculens.application`` and ``doculens.domain``.
"""

from importlib.metadata import version
from typing import Final

SERVICE_NAME: Final = "doculens-api"
__version__ = version("doculens-api")
