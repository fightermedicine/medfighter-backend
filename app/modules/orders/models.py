"""Orders ORM models (§10, §11, §12, §34, §35).

Tables owned:
- orders
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


def _utc_now() -> datetime:
    return datetime.now(UTC)


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("products.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    quantity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    amount_piastres: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="EGP", nullable=False)
    transaction_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("ledger_transactions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(32), default="COMPLETED", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, nullable=False
    )

    purchase_units: Mapped[list[PurchaseUnit]] = relationship(
        "PurchaseUnit", back_populates="order", cascade="all, delete-orphan", lazy="selectin"
    )


class PurchaseUnit(Base):
    __tablename__ = "purchase_units"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("products.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    unit_index: Mapped[int] = mapped_column(Integer, nullable=False)  # 1..quantity
    status: Mapped[str] = mapped_column(
        String(32), default="ASSIGNED", nullable=False
    )  # ASSIGNED, UNASSIGNED, GIFTED
    assigned_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    entitlement_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("entitlements.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, nullable=False
    )

    order: Mapped[Order] = relationship("Order", back_populates="purchase_units")
