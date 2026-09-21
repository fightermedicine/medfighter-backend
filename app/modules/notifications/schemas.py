"""Notifications Pydantic schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class BroadcastNotificationRequest(BaseModel):
    title: str = Field(min_length=2, max_length=255)
    message: str = Field(min_length=3)
    notification_type: str = Field(default="ANNOUNCEMENT", pattern="^(NEW_PDF|NEW_COURSE|ANNOUNCEMENT|SYSTEM)$")
    target_medical_year: int = Field(default=0, ge=0, le=5)
    resource_type: str | None = None
    resource_id: str | None = None


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    message: str
    notification_type: str
    target_medical_year: int
    resource_type: str | None
    resource_id: str | None
    created_at: datetime
    is_read: bool = False
