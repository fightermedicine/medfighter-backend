"""Wallet and double-entry ledger schema migration (§7, §8, §9, §10, §35).

Revision ID: 0003_wallet_ledger
Revises: 0002_identity_and_audit
Create Date: 2026-09-20
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0003_wallet_ledger"
down_revision = "0002_identity_and_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Wallet Accounts table
    op.create_table(
        "wallet_accounts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column(
            "account_type", sa.String(length=32), nullable=False, server_default="USER_WALLET"
        ),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="EGP"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_wallet_accounts_user_id", "wallet_accounts", ["user_id"])
    op.create_index(
        "ix_wallet_accounts_user_currency", "wallet_accounts", ["user_id", "currency"], unique=True
    )

    # 2. Ledger Transactions table
    op.create_table(
        "ledger_transactions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("reference", sa.String(length=128), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="PENDING"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ledger_transactions_idempotency",
        "ledger_transactions",
        ["idempotency_key"],
        unique=True,
    )
    op.create_index("ix_ledger_transactions_reference", "ledger_transactions", ["reference"])

    # 3. Ledger Entries table
    op.create_table(
        "ledger_entries",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("transaction_id", sa.UUID(), nullable=False),
        sa.Column("account_id", sa.UUID(), nullable=False),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("amount_piastres", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["account_id"], ["wallet_accounts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["transaction_id"], ["ledger_transactions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ledger_entries_transaction_id", "ledger_entries", ["transaction_id"])
    op.create_index("ix_ledger_entries_account_id", "ledger_entries", ["account_id"])

    # 4. Deposits table
    op.create_table(
        "deposits",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("amount_piastres", sa.BigInteger(), nullable=False),
        sa.Column("method", sa.String(length=32), nullable=False, server_default="vodafone_cash"),
        sa.Column("sender_phone", sa.String(length=32), nullable=False),
        sa.Column("reference_code", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="PENDING"),
        sa.Column("transaction_id", sa.UUID(), nullable=True),
        sa.Column("reviewed_by", sa.UUID(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejection_reason", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["reviewed_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["transaction_id"], ["ledger_transactions.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_deposits_user_id", "deposits", ["user_id"])
    op.create_index("ix_deposits_reference_code", "deposits", ["reference_code"], unique=True)


def downgrade() -> None:
    op.drop_table("deposits")
    op.drop_table("ledger_entries")
    op.drop_table("ledger_transactions")
    op.drop_table("wallet_accounts")
