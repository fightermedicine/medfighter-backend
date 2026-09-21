"""Curriculum ORM models for dynamic medical year hierarchy.

Hierarchy:
Medical Year (1..5) -> Modules -> Subjects -> Custom Subfolders.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


def _utc_now() -> datetime:
    return datetime.now(UTC)


class CurriculumFolder(Base):
    __tablename__ = "curriculum_folders"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    medical_year: Mapped[int] = mapped_column(Integer, default=1, nullable=False, index=True)  # 1 to 5
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("curriculum_folders.id", ondelete="CASCADE"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # folder_type: MODULE, SUBJECT, CUSTOM
    folder_type: Mapped[str] = mapped_column(String(32), default="CUSTOM", nullable=False)
    icon: Mapped[str | None] = mapped_column(String(64), nullable=True)
    order_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, onupdate=_utc_now, nullable=False
    )

    children: Mapped[list[CurriculumFolder]] = relationship(
        "CurriculumFolder",
        back_populates="parent",
        cascade="all, delete-orphan",
        order_by="CurriculumFolder.order_index",
    )
    parent: Mapped[CurriculumFolder | None] = relationship(
        "CurriculumFolder",
        back_populates="children",
        remote_side=[id],
    )
