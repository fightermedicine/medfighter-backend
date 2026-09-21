"""Catalog ORM models (§10, §11, §17, §34, §64).

Tables owned:
- products
- product_versions
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


def _utc_now() -> datetime:
    return datetime.now(UTC)


class Product(Base):
    __tablename__ = "products"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    price_piastres: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="EGP", nullable=False)
    category: Mapped[str] = mapped_column(String(64), default="medical", nullable=False)
    product_type: Mapped[str] = mapped_column(
        String(32), default="memo", nullable=False
    )  # memo, book, course, bundle, mcq_bank, deck
    medical_year: Mapped[int] = mapped_column(Integer, default=1, nullable=False, index=True)
    folder_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("curriculum_folders.id", ondelete="SET NULL"), nullable=True
    )
    preview_data: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, nullable=False
    )

    versions: Mapped[list[ProductVersion]] = relationship(
        "ProductVersion", back_populates="product", cascade="all, delete-orphan", lazy="selectin"
    )
    price_rules: Mapped[list[PriceRule]] = relationship(
        "PriceRule", back_populates="product", cascade="all, delete-orphan", lazy="selectin"
    )
    bundle: Mapped[Bundle | None] = relationship(
        "Bundle",
        back_populates="product",
        uselist=False,
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class ProductVersion(Base):
    __tablename__ = "product_versions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    product_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    changelog: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, nullable=False
    )

    product: Mapped[Product] = relationship("Product", back_populates="versions")


class PriceRule(Base):
    __tablename__ = "price_rules"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    product_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    min_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    discount_percent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, nullable=False
    )

    product: Mapped[Product] = relationship("Product", back_populates="price_rules")


class Bundle(Base):
    __tablename__ = "bundles"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    product_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, nullable=False
    )

    product: Mapped[Product] = relationship("Product", back_populates="bundle")
    items: Mapped[list[BundleItem]] = relationship(
        "BundleItem", back_populates="bundle", cascade="all, delete-orphan", lazy="selectin"
    )


class BundleItem(Base):
    __tablename__ = "bundle_items"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    bundle_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("bundles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    item_product_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    bundle: Mapped[Bundle] = relationship("Bundle", back_populates="items")
    item_product: Mapped[Product] = relationship("Product", foreign_keys=[item_product_id])
