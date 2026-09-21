"""Wallet request and response schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DepositRequest(BaseModel):
    amount_egp: int = Field(gt=0, le=100_000, description="Amount in whole EGP")
    sender_phone: str = Field(min_length=11, max_length=15, pattern=r"^\+?[0-9]+$")
    reference_code: str = Field(min_length=4, max_length=64)


class DepositResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    amount_piastres: int
    amount_egp: float
    method: str
    sender_phone: str
    reference_code: str
    status: str
    created_at: datetime
    reviewed_at: datetime | None = None
    rejection_reason: str | None = None


class WalletBalanceResponse(BaseModel):
    user_id: uuid.UUID
    currency: str
    balance_piastres: int
    balance_egp: float
    formatted: str


class RejectDepositRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=255)


class AdminManualTopUpRequest(BaseModel):
    user_identifier: str = Field(min_length=3, max_length=255, description="Student email or phone or user ID")
    amount_egp: float = Field(gt=0, le=100_000, description="Amount in EGP")
    note: str = Field(default="Admin manual top-up", max_length=255)


class AdminManualTopUpResponse(BaseModel):
    user_id: uuid.UUID
    email: str
    amount_egp: float
    new_balance_egp: float
    transaction_id: uuid.UUID
    message: str
