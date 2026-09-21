"""Curriculum Pydantic schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CurriculumFolderCreate(BaseModel):
    medical_year: int = Field(ge=1, le=6)
    parent_id: uuid.UUID | None = None
    name: str = Field(min_length=1, max_length=255)
    folder_type: str = Field(default="CUSTOM", pattern="^(MODULE|SUBJECT|CUSTOM)$")
    icon: str | None = Field(default=None, max_length=64)
    order_index: int = Field(default=0, ge=0)


class CurriculumFolderUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    parent_id: uuid.UUID | None = None
    folder_type: str | None = Field(default=None, pattern="^(MODULE|SUBJECT|CUSTOM)$")
    icon: str | None = None
    order_index: int | None = None


class CurriculumFolderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    medical_year: int
    parent_id: uuid.UUID | None
    name: str
    folder_type: str
    icon: str | None
    order_index: int
    created_at: datetime
    updated_at: datetime
    child_count: int = 0
    children: list[CurriculumFolderOut] = Field(default_factory=list)
