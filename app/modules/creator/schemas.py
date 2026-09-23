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
    amount_egp: float = Field(..., ge=1.0, le=10000.0, description="Amount to credit in EGP")
    note: str = Field("Creator Booklet Credit", max_length=255, description="Reason / booklet description")


class CreatorTopupResponse(BaseModel):
    success: bool = True
    student_id: uuid.UUID | str
    student_name: str
    student_email: str
    student_phone: str | None = None
    amount_egp: float
    new_balance_egp: float
    transaction_id: str | uuid.UUID
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
    total_booklets_published: int = 0
    medzone_booklet_price: float = 0.0
    assigned_folder_id: str | None = None
    assigned_folder_name: str | None = None
    assigned_medical_year: int | None = None
    recent_topups: list[CreatorTopupHistoryItem]
