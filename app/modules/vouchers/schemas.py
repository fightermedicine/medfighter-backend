"""Voucher Pydantic schemas (§11, §35)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class VoucherBatchCreate(BaseModel):
    name: str = Field(..., min_length=3, max_length=255, description="Descriptive batch name")
    count: int = Field(1, ge=1, le=1000, description="Number of voucher codes to mint")
    voucher_type: str = Field("WALLET_CREDIT", pattern="^(WALLET_CREDIT|COURSE_UNLOCK)$")
    credit_amount_egp: float | None = Field(
        None, ge=1.0, description="Value in EGP for WALLET_CREDIT"
    )
    product_id: uuid.UUID | None = Field(None, description="Course/Product ID for COURSE_UNLOCK")
    max_redemptions_per_code: int = Field(1, ge=1, le=10000)
    expires_in_days: int | None = Field(None, ge=1, le=365)


class VoucherOut(BaseModel):
    id: uuid.UUID
    code: str
    voucher_type: str
    credit_piastres: int
    credit_egp: float
    product_id: uuid.UUID | None
    max_redemptions: int
    redemptions_count: int
    is_active: bool
    expires_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class VoucherBatchOut(BaseModel):
    id: uuid.UUID
    name: str
    batch_code: str
    vouchers_count: int
    created_at: datetime
    vouchers: list[VoucherOut] = []


class RedeemVoucherRequest(BaseModel):
    code: str = Field(
        ..., min_length=8, max_length=32, description="16-character alphanumeric voucher code"
    )


class RedeemVoucherResponse(BaseModel):
    success: bool
    voucher_type: str
    message: str
    credited_egp: float | None = None
    new_balance_egp: float | None = None
    unlocked_product_id: uuid.UUID | None = None
    unlocked_product_title: str | None = None
