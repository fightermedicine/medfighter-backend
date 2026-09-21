"""Add payments table.

Revision ID: 0005_payments
Revises: 0004_catalog_orders_entitlements
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005_payments"
down_revision = "0004_catalog_orders_entitlements"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "payments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("reference", sa.String(24), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_id", sa.Uuid(), sa.ForeignKey("products.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("amount_piastres", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="EGP"),
        sa.Column("method", sa.String(32), nullable=False, server_default="vodafone_cash"),
        sa.Column("status", sa.String(32), nullable=False, server_default="CREATED"),
        sa.Column("proof_path", sa.Text(), nullable=True),
        sa.Column("proof_hash", sa.String(64), nullable=True),
        sa.Column("proof_content_type", sa.String(64), nullable=True),
        sa.Column("reviewed_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("entitlement_id", sa.Uuid(), sa.ForeignKey("entitlements.id", ondelete="SET NULL"), nullable=True),
        sa.Column("ledger_transaction_id", sa.Uuid(), sa.ForeignKey("ledger_transactions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_payments_reference", "payments", ["reference"], unique=True)
    op.create_index("ix_payments_user_id", "payments", ["user_id"])
    op.create_index("ix_payments_product_id", "payments", ["product_id"])
    op.create_index("ix_payments_status", "payments", ["status"])
    op.create_index("ix_payments_proof_hash", "payments", ["proof_hash"])
    op.create_index(
        "ix_payments_user_product_active",
        "payments",
        ["user_id", "product_id", "status"],
    )


def downgrade() -> None:
    op.drop_table("payments")
