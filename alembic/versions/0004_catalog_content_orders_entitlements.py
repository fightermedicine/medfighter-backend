"""Catalog, content, orders, and entitlements migration (§10, §11, §12, §16, §17, §18).

Revision ID: 0004_catalog_orders_entitlements
Revises: 0003_wallet_ledger
Create Date: 2026-09-20
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0004_catalog_orders_entitlements"
down_revision = "0003_wallet_ledger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Products table
    op.create_table(
        "products",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("price_piastres", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="EGP"),
        sa.Column("category", sa.String(length=64), nullable=False, server_default="medical"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # 2. Product Versions table
    op.create_table(
        "product_versions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("product_id", sa.UUID(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("changelog", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_product_versions_product_id", "product_versions", ["product_id"])

    # 3. Content Assets table
    op.create_table(
        "content_assets",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("product_id", sa.UUID(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=32), nullable=False, server_default="pdf"),
        sa.Column("storage_path", sa.String(length=512), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("is_encrypted", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "encryption_algorithm",
            sa.String(length=32),
            nullable=False,
            server_default="AES-256-GCM",
        ),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_content_assets_product_id", "content_assets", ["product_id"])

    # 4. Orders table
    op.create_table(
        "orders",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("product_id", sa.UUID(), nullable=False),
        sa.Column("amount_piastres", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="EGP"),
        sa.Column("transaction_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="COMPLETED"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["transaction_id"], ["ledger_transactions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_orders_user_id", "orders", ["user_id"])
    op.create_index("ix_orders_product_id", "orders", ["product_id"])
    op.create_index("ix_orders_transaction_id", "orders", ["transaction_id"])

    # 5. Entitlements table
    op.create_table(
        "entitlements",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("product_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="ACTIVE"),
        sa.Column(
            "granted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_entitlements_user_id", "entitlements", ["user_id"])
    op.create_index("ix_entitlements_product_id", "entitlements", ["product_id"])
    op.create_index(
        "ix_entitlements_user_product", "entitlements", ["user_id", "product_id"], unique=True
    )

    # 6. Device Licenses table
    op.create_table(
        "device_licenses",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("entitlement_id", sa.UUID(), nullable=False),
        sa.Column("device_id", sa.UUID(), nullable=False),
        sa.Column("license_token", sa.Text(), nullable=False),
        sa.Column("wrapped_cek", sa.Text(), nullable=False),
        sa.Column(
            "issued_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["entitlement_id"], ["entitlements.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_device_licenses_device_id", "device_licenses", ["device_id"])
    op.create_index("ix_device_licenses_entitlement_id", "device_licenses", ["entitlement_id"])
    op.create_index(
        "ix_device_licenses_entitlement_device",
        "device_licenses",
        ["entitlement_id", "device_id"],
    )


def downgrade() -> None:
    op.drop_table("device_licenses")
    op.drop_table("entitlements")
    op.drop_table("orders")
    op.drop_table("content_assets")
    op.drop_table("product_versions")
    op.drop_table("products")
