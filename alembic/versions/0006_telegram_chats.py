"""Add telegram_chats table for bot registration.

Revision ID: 0006_telegram_chats
Revises: 0005_payments
Create Date: 2026-09-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision = "0006_telegram_chats"
down_revision = "0005_payments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "telegram_chats",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("chat_type", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=256), nullable=True),
        sa.Column("username", sa.String(length=128), nullable=True),
        sa.Column(
            "linked_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("is_admin_channel", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "registered_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("chat_id"),
    )
    op.create_index("ix_telegram_chats_chat_id", "telegram_chats", ["chat_id"])
    op.create_index("ix_telegram_chats_linked_user_id", "telegram_chats", ["linked_user_id"])


def downgrade() -> None:
    op.drop_index("ix_telegram_chats_linked_user_id", table_name="telegram_chats")
    op.drop_index("ix_telegram_chats_chat_id", table_name="telegram_chats")
    op.drop_table("telegram_chats")
