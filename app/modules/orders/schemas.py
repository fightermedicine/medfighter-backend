"""Orders Pydantic schemas (§10, §11, §12)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CheckoutRequest(BaseModel):
    product_id: uuid.UUID
    quantity: int = Field(default=1, ge=1, le=10, description="Number of copies to purchase")
    idempotency_key: str | None = None


class PurchaseUnitResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    unit_index: int
    status: str
    assigned_user_id: uuid.UUID | None = None
    entitlement_id: uuid.UUID | None = None


class OrderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    product_id: uuid.UUID
    product_title: str | None = None
    quantity: int = 1
    amount_piastres: int
    amount_egp: float
    currency: str = "EGP"
    transaction_id: uuid.UUID
    status: str
    created_at: datetime
    purchase_units: list[PurchaseUnitResponse] = []
