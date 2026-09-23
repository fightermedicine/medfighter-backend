"""Add exam_mode and show_explanations to question_banks.

Revision ID: 0007_exam_mode
Revises: 0006_telegram_chats
Create Date: 2026-09-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision = "0007_exam_mode"
down_revision = "0006_telegram_chats"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "question_banks",
        sa.Column(
            "exam_mode",
            sa.String(length=32),
            nullable=False,
            server_default="PRACTICE",
        ),
    )
    op.add_column(
        "question_banks",
        sa.Column(
            "show_explanations",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )


def downgrade() -> None:
    op.drop_column("question_banks", "show_explanations")
    op.drop_column("question_banks", "exam_mode")
