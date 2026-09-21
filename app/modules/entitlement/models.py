"""Entitlement and Device License ORM models (§13, §16, §17, §18, §34).

Tables owned:
- entitlements
- device_licenses

Invariants:
- Entitlements represent content ownership granted by the server upon purchase.
- DeviceLicenses represent short-lived cryptographic authorization bound to an authorized device.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


def _utc_now() -> datetime:
    return datetime.now(UTC)


class Entitlement(Base):
    __tablename__ = "entitlements"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(
        String(32), default="ACTIVE", nullable=False
    )  # ACTIVE, EXPIRED, REVOKED
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    licenses: Mapped[list[DeviceLicense]] = relationship(
        "DeviceLicense", back_populates="entitlement", cascade="all, delete-orphan"
    )

    __table_args__ = (Index("ix_entitlements_user_product", "user_id", "product_id", unique=True),)


class DeviceLicense(Base):
    __tablename__ = "device_licenses"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    entitlement_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("entitlements.id", ondelete="CASCADE"), nullable=False, index=True
    )
    device_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True
    )
    license_token: Mapped[str] = mapped_column(Text, nullable=False)
    wrapped_cek: Mapped[str] = mapped_column(Text, nullable=False)
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    entitlement: Mapped[Entitlement] = relationship("Entitlement", back_populates="licenses")

    __table_args__ = (
        Index("ix_device_licenses_entitlement_device", "entitlement_id", "device_id"),
    )
