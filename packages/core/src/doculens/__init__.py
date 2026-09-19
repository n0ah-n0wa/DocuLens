"""DocuLens core: the domain, application and infrastructure layers.

Dependency direction (SPECIFICATIONS.md §72)::

    interfaces (apps/api, services/document-worker)
        └─> application ──> domain
                 ▲
        infrastructure (implements the ports declared by the inner layers)

``domain`` and ``application`` must not import web frameworks, cloud SDKs, vector stores,
LangChain or persistence libraries. ``tests/unit/test_architecture.py`` enforces this.
"""

from importlib.metadata import version

__version__ = version("doculens-core")
