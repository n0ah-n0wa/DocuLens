"""full-text search index over chunk text

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-21 23:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Keyword retrieval (§18, OQ-4): the expression must match the one the repository queries.
    op.create_index(
        "ix_document_chunks_text_search",
        "document_chunks",
        [sa.text("to_tsvector('simple'::regconfig, text)")],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_document_chunks_text_search", table_name="document_chunks")
