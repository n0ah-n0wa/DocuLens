"""PostgreSQL persistence: SQLAlchemy 2 ORM models, mappers, repositories and the unit of work.

Only this package knows about SQLAlchemy. The ``models`` module is the single source of the schema
that Alembic migrations (``packages/core/alembic``) are generated from and checked against.
"""
