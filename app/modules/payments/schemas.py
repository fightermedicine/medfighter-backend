"""Payments Pydantic schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, field_validator


class InitiatePaymentRequest(BaseModel):
    product_id: uuid.UUID


class PaymentResponse(BaseModel):
    id: uuid.UUID
    reference: str
    user_id: uuid.UUID
    product_id: uuid.UUID | None = None
    product_title: str | None = None
    amount_piastres: int
    amount_egp: float
    currency: str
    method: str
    status: str
    rejection_reason: str | None = None
    submitted_at: datetime | None = None
    reviewed_at: datetime | None = None
    expires_at: datetime
    created_at: datetime

    # Merchant instructions (only in CREATED state response)
    merchant_phone: str | None = None

    model_config = {"from_attributes": True}


class SubmitProofResponse(PaymentResponse):
    pass


class RejectPaymentRequest(BaseModel):
    reason: str

    @field_validator("reason")
    @classmethod
    def reason_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Rejection reason cannot be empty")
        return v.strip()


class AdminPaymentDetail(PaymentResponse):
    """Extended detail for admin view — includes proof URL."""
    proof_url: str | None = None
    user_email: str | None = None
    user_name: str | None = None


class PendingPaymentsResponse(BaseModel):
    total: int
    items: list[AdminPaymentDetail]
