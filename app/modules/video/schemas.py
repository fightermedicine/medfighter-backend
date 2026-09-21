"""Video request and response schemas (§33, §34)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class VideoAssetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    product_id: uuid.UUID
    vimeo_video_id: str
    title: str
    duration_seconds: int
    created_at: datetime


class VideoSessionRequest(BaseModel):
    device_id: uuid.UUID


class VideoSessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    video_asset_id: uuid.UUID
    title: str
    embed_url: str
    playback_token: str
    expires_at: datetime
