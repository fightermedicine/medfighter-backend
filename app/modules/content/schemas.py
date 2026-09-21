"""Content annotation request and response schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CreateAnnotationRequest(BaseModel):
    page_number: int = Field(ge=1)
    annotation_type: str = Field(min_length=2, max_length=32)
    data_json: str = Field(min_length=2)


class AnnotationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    content_asset_id: uuid.UUID
    page_number: int
    annotation_type: str
    data_json: str
    created_at: datetime
    updated_at: datetime
