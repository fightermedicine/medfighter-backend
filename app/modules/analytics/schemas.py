"""Analytics Pydantic schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


# ── User request ──────────────────────────────────────────────────────────────

class PdfReadEventRequest(BaseModel):
    product_id: uuid.UUID
    pages_viewed: int = Field(default=0, ge=0)


# ── Admin response rows ───────────────────────────────────────────────────────

class PdfReaderRow(BaseModel):
    product_id: uuid.UUID
    product_title: str
    user_id: uuid.UUID
    user_name: str
    user_email: str
    open_count: int
    pages_viewed: int
    first_opened_at: datetime
    last_opened_at: datetime

    model_config = {"from_attributes": True}


class PdfReaderReport(BaseModel):
    total: int
    items: list[PdfReaderRow]


# ── Purchase / order row for finance report ───────────────────────────────────

class PurchaseRow(BaseModel):
    order_id: uuid.UUID
    user_id: uuid.UUID
    user_name: str
    user_email: str
    product_id: uuid.UUID
    product_title: str
    quantity: int
    amount_egp: float
    status: str
    method: str          # "wallet" | "vodafone_cash"
    purchased_at: datetime

    model_config = {"from_attributes": True}


class PurchasesReport(BaseModel):
    total: int
    items: list[PurchaseRow]


# ── Wallet balance row ────────────────────────────────────────────────────────

class WalletBalanceRow(BaseModel):
    user_id: uuid.UUID
    user_name: str
    user_email: str
    balance_egp: float
    account_id: uuid.UUID

    model_config = {"from_attributes": True}


class WalletBalancesReport(BaseModel):
    total: int
    items: list[WalletBalanceRow]


# ── Ledger history row ────────────────────────────────────────────────────────

class LedgerHistoryRow(BaseModel):
    transaction_id: uuid.UUID
    reference: str
    description: str
    direction: str         # CREDIT / DEBIT
    amount_egp: float
    status: str
    posted_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class UserLedgerHistory(BaseModel):
    user_id: uuid.UUID
    user_name: str
    user_email: str
    balance_egp: float
    transactions: list[LedgerHistoryRow]


# ── Non-purchasers row ────────────────────────────────────────────────────────

class NonPurchaserRow(BaseModel):
    user_id: uuid.UUID
    user_name: str
    user_email: str
    medical_year: int
    created_at: datetime

    model_config = {"from_attributes": True}


class NonPurchasersReport(BaseModel):
    product_id: uuid.UUID
    product_title: str
    total_users: int
    non_purchasers: int
    items: list[NonPurchaserRow]


# ── Audit log row ─────────────────────────────────────────────────────────────

class AuditEventOut(BaseModel):
    id: uuid.UUID
    actor_id: uuid.UUID | None
    actor_role: str | None
    actor_name: str | None     # denormalized from users join
    actor_email: str | None    # denormalized from users join
    action: str
    resource_type: str
    resource_id: str | None
    details: dict | None
    ip_address: str | None
    result: str
    created_at: datetime

    model_config = {"from_attributes": True}


class AuditLogResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[AuditEventOut]
