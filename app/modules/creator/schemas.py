"""Schemas for Creator Studio endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime
from pydantic import BaseModel, Field


class CreatorStudentLookupRequest(BaseModel):
    identifier: str = Field(..., min_length=2, max_length=255, description="Student phone, email, or user ID")


class CreatorStudentLookupResponse(BaseModel):
    id: uuid.UUID
    full_name: str
    email: str
    phone: str | None = None
    medical_year: int
    current_balance_egp: float
    is_active: bool


class CreatorTopupRequest(BaseModel):
    identifier: str = Field(..., min_length=2, max_length=255, description="Student phone, email, or user ID")
    amount_egp: float = Field(55.0, ge=1.0, le=10000.0, description="Amount to credit in EGP")
    note: str = Field("Medzone Ortho Booklet - Dr. Ahmed Talaat", max_length=255, description="Reason / booklet description")


class CreatorTopupResponse(BaseModel):
    success: bool = True
    student_id: uuid.UUID
    student_name: str
    student_email: str
    student_phone: str | None = None
    amount_egp: float
    new_balance_egp: float
    transaction_id: str
    timestamp: str
    whatsapp_message: str


class CreatorTopupHistoryItem(BaseModel):
    id: str
    transaction_id: str
    student_name: str
    student_identifier: str
    amount_egp: float
    note: str
    created_at: str


class CreatorDashboardResponse(BaseModel):
    creator_id: uuid.UUID
    creator_name: str
    creator_email: str
    total_students_credited: int
    total_amount_credited_egp: float
    medzone_booklet_price: float = 55.0
    recent_topups: list[CreatorTopupHistoryItem]
