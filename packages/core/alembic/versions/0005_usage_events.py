"""usage events ledger for quotas

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-29 14:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "usage_events",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("owner_id", sa.UUID(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("usage_day", sa.Date(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("questions", sa.Integer(), server_default="0", nullable=False),
        sa.Column("cost_usd_micros", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("questions >= 0", name=op.f("ck_usage_events_questions_non_negative")),
        sa.CheckConstraint("cost_usd_micros >= 0", name=op.f("ck_usage_events_cost_non_negative")),
        sa.CheckConstraint(
            "char_length(idempotency_key) BETWEEN 1 AND 128",
            name=op.f("ck_usage_events_idempotency_key_len"),
        ),
        sa.CheckConstraint(
            "char_length(kind) BETWEEN 1 AND 32", name=op.f("ck_usage_events_kind_len")
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_usage_events_owner_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_usage_events")),
        sa.UniqueConstraint("owner_id", "idempotency_key", name=op.f("uq_usage_events_owner_key")),
    )
    op.create_index(
        op.f("ix_usage_events_owner_id_usage_day"),
        "usage_events",
        ["owner_id", "usage_day"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_usage_events_owner_id_usage_day"), table_name="usage_events")
    op.drop_table("usage_events")
