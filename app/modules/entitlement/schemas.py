"""Entitlements and Device Licenses Pydantic schemas (§13, §16, §17, §18)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class EntitlementResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    product_id: uuid.UUID
    status: str
    granted_at: datetime
    expires_at: datetime | None = None


class LicenseRequest(BaseModel):
    device_id: uuid.UUID
    content_asset_id: uuid.UUID


class LicenseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    entitlement_id: uuid.UUID
    device_id: uuid.UUID
    license_token: str
    wrapped_cek: str
    issued_at: datetime
    expires_at: datetime
