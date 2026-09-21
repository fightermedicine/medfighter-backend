"""SQLAlchemy model for persisted Telegram chats (users and groups)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class TelegramChat(Base):
    """Represents a Telegram chat (private DM or group) that has interacted with the bot.

    - Private chats belonging to admins (matched by username) have ``is_admin_channel=True``.
    - Private chats belonging to students have ``linked_user_id`` set after email verification.
    - Group/supergroup chats that sent ``/start`` have ``is_admin_channel=True``.
    """

    __tablename__ = "telegram_chats"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    # Telegram's own numeric identifier — unique across all chat types.
    chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)

    # "private" | "group" | "supergroup" | "channel"
    chat_type: Mapped[str] = mapped_column(String(32), nullable=False)

    # Human-readable label: group title or user full_name.
    title: Mapped[str | None] = mapped_column(String(256), nullable=True)

    # @username (without the @), if present.
    username: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # Set once the student replies with their registered email.
    linked_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # True for admin DMs and all groups that activated the bot.
    is_admin_channel: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    registered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
