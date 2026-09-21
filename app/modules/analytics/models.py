"""Analytics ORM models.

Tables owned:
- pdf_read_events  — per-user, per-product PDF reader tracking (upsert on open)

Invariant: one row per (user_id, product_id) — incremented atomically on each
open via INSERT ... ON CONFLICT DO UPDATE.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


def _utc_now() -> datetime:
    return datetime.now(UTC)


class PdfReadEvent(Base):
    __tablename__ = "pdf_read_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Cumulative counters
    open_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    pages_viewed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Timestamps
    first_opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, nullable=False
    )
    last_opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, nullable=False
    )

    __table_args__ = (
        Index("ix_pdf_read_events_user_product", "user_id", "product_id", unique=True),
    )
