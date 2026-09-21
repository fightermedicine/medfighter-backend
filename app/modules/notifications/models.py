"""Notifications ORM models."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


def _utc_now() -> datetime:
    return datetime.now(UTC)


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    # NEW_PDF, NEW_COURSE, ANNOUNCEMENT, SYSTEM
    notification_type: Mapped[str] = mapped_column(String(32), default="ANNOUNCEMENT", nullable=False)
    # 0 = All medical years, 1..5 = Specific year
    target_medical_year: Mapped[int] = mapped_column(Integer, default=0, nullable=False, index=True)
    resource_type: Mapped[str | None] = mapped_column(String(32), nullable=True)  # course, pdf, quiz, deck
    resource_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, nullable=False, index=True
    )


class UserNotificationRead(Base):
    __tablename__ = "user_notification_reads"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    notification_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("notifications.id", ondelete="CASCADE"), nullable=False, index=True
    )
    read_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, nullable=False
    )

    __table_args__ = (
        Index("ix_user_notification_unique", "user_id", "notification_id", unique=True),
    )
