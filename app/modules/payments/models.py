"""Payments ORM models — controlled Vodafone Cash payment verification workflow.

Tables owned:
- payments

State machine:
  CREATED → UNDER_REVIEW → APPROVED  (terminal, entitlement granted)
                         → REJECTED  (terminal, reason required)
                         → MORE_PROOF_REQUIRED → UNDER_REVIEW
  CREATED → EXPIRED (48h TTL)
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _expires_at() -> datetime:
    return datetime.now(UTC) + timedelta(hours=48)


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    # Server-generated human-readable reference e.g. FIGHTER-8K3P2
    reference: Mapped[str] = mapped_column(String(24), unique=True, index=True, nullable=False)

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("products.id", ondelete="SET NULL"), nullable=True, index=True
    )
    amount_piastres: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="EGP", nullable=False)
    method: Mapped[str] = mapped_column(String(32), default="vodafone_cash", nullable=False)

    # State machine
    # CREATED | UNDER_REVIEW | APPROVED | REJECTED | MORE_PROOF_REQUIRED | EXPIRED
    status: Mapped[str] = mapped_column(String(32), default="CREATED", nullable=False, index=True)

    # Proof storage
    proof_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    proof_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)  # SHA-256 hex
    proof_content_type: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Admin review
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Result links — set atomically on APPROVED
    entitlement_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("entitlements.id", ondelete="SET NULL"), nullable=True
    )
    ledger_transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("ledger_transactions.id", ondelete="SET NULL"), nullable=True
    )

    # Timestamps
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_expires_at, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, nullable=False
    )

    __table_args__ = (
        # One active payment per user+product (prevents spam)
        Index(
            "ix_payments_user_product_active",
            "user_id",
            "product_id",
            "status",
        ),
    )
