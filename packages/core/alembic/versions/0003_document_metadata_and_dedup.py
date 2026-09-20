"""document metadata and active-content uniqueness

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-20 18:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    # One live document per (owner, content) pair (§49; duplicate policy OQ-6, provisional).
    op.create_index(
        "uq_documents_owner_id_content_hash_active",
        "documents",
        ["owner_id", "content_hash"],
        unique=True,
        postgresql_where=sa.text("processing_status <> 'DELETED'"),
    )


def downgrade() -> None:
    op.drop_index("uq_documents_owner_id_content_hash_active", table_name="documents")
    op.drop_column("documents", "metadata")
