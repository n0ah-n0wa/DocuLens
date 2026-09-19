"""Application layer: use cases and services that orchestrate the domain through its ports.

Depends on ``doculens.domain`` only. Concrete adapters are injected by the interface layer
(``apps/api``, ``services/document-worker``); nothing here may import ``doculens.infrastructure``.
"""
