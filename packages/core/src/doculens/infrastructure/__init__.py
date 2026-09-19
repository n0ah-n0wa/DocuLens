"""Infrastructure layer: adapters implementing the ports declared by the inner layers.

This is the only layer of the core package allowed to depend on PostgreSQL/SQLAlchemy, S3,
ChromaDB, Redis, queue clients and AI provider SDKs (SPECIFICATIONS.md §72 and §73).
"""
