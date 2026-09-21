"""Voucher ORM models (§11, §35).

Tables owned:
- voucher_batches
- vouchers
- voucher_redemptions
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


def _utc_now() -> datetime:
    return datetime.now(UTC)


class VoucherBatch(Base):
    __tablename__ = "voucher_batches"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    batch_code: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, nullable=False
    )

    vouchers: Mapped[list[Voucher]] = relationship(
        "Voucher", back_populates="batch", cascade="all, delete-orphan", lazy="selectin"
    )


class Voucher(Base):
    __tablename__ = "vouchers"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    batch_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("voucher_batches.id", ondelete="SET NULL"), nullable=True
    )
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    # Type: "WALLET_CREDIT" or "COURSE_UNLOCK"
    voucher_type: Mapped[str] = mapped_column(String(32), nullable=False, default="WALLET_CREDIT")
    credit_piastres: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("products.id", ondelete="SET NULL"), nullable=True
    )
    max_redemptions: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    redemptions_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, nullable=False
    )

    batch: Mapped[VoucherBatch | None] = relationship(
        "VoucherBatch", back_populates="vouchers", lazy="selectin"
    )
    redemptions: Mapped[list[VoucherRedemption]] = relationship(
        "VoucherRedemption", back_populates="voucher", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (Index("ix_vouchers_code_active", "code", "is_active"),)


class VoucherRedemption(Base):
    __tablename__ = "voucher_redemptions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    voucher_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("vouchers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    redeemed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, nullable=False
    )
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)

    voucher: Mapped[Voucher] = relationship(
        "Voucher", back_populates="redemptions", lazy="selectin"
    )
