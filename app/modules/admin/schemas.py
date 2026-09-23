"""Admin module schemas (§38–§40)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator


class AdminStatsOut(BaseModel):
    total_students: int
    total_revenue_egp: float
    pending_deposits_count: int
    active_courses_count: int
    active_vouchers_count: int


class AdminUserDeviceOut(BaseModel):
    id: uuid.UUID
    device_fingerprint: str
    platform: str | None = None
    model: str | None = None
    status: str
    registered_at: datetime
    last_seen_at: datetime | None = None


class AdminUserEntitlementOut(BaseModel):
    id: uuid.UUID
    product_id: uuid.UUID
    product_title: str
    status: str
    granted_at: datetime
    expires_at: datetime | None = None


class AdminUserOut(BaseModel):
    id: uuid.UUID
    full_name: str
    email: str
    phone: str | None = None
    gender: str | None = None
    medical_year: int = 1
    is_verified: bool = False
    roles: list[str] = Field(default_factory=list)
    wallet_balance_egp: float
    devices_count: int
    entitlements_count: int = 0
    is_active: bool
    created_at: datetime
    devices: list[AdminUserDeviceOut] = Field(default_factory=list)
    entitlements: list[AdminUserEntitlementOut] = Field(default_factory=list)


class AdminUserStatusRequest(BaseModel):
    is_active: bool
    reason: str = Field("Admin status update", min_length=1, max_length=255)


class AdminGrantEntitlementRequest(BaseModel):
    product_id: uuid.UUID
    reason: str = Field("Admin manual grant", min_length=1, max_length=255)
    expires_at: datetime | None = None


class AdminTopUpRequest(BaseModel):
    amount_egp: float = Field(
        ..., ge=1.0, le=100000.0, description="Amount in EGP to credit to student wallet"
    )
    reason: str = Field("Admin manual account top up", min_length=3, max_length=255)


class AdminTopUpResponse(BaseModel):
    success: bool
    credited_egp: float
    new_balance_egp: float
    transaction_id: uuid.UUID
    message: str


class AdminTopDownRequest(BaseModel):
    amount_egp: float = Field(
        ..., ge=0.5, le=100000.0, description="Amount in EGP to deduct from student wallet"
    )
    reason: str = Field("Admin manual balance deduction", min_length=3, max_length=255)


class AdminTopDownResponse(BaseModel):
    success: bool
    debited_egp: float
    new_balance_egp: float
    transaction_id: uuid.UUID
    message: str


class AdminDeviceOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    user_name: str
    user_email: str
    device_fingerprint: str
    model: str | None
    platform: str | None
    status: str
    registered_at: datetime


class EmergencyLockRequest(BaseModel):
    identifier: str = Field(
        ..., min_length=3, description="Phone number, email, or user UUID of the leaker"
    )
    reason: str = Field(..., min_length=5, description="Evidence of leak or piracy")


class EmergencyLockResponse(BaseModel):
    success: bool
    user_id: uuid.UUID
    user_name: str
    devices_revoked: int
    sessions_revoked: int
    message: str


# --- Creator Content Authoring Schemas ---


class AdminCourseCreateRequest(BaseModel):
    title: str = Field(..., min_length=2, max_length=255)
    description: str = Field(..., min_length=2)
    price_egp: float = Field(..., ge=0.0, description="Price in Egyptian Pounds")
    category: str = Field("Medical", max_length=64)
    product_type: str = Field("course", max_length=32)
    medical_year: int = Field(1, ge=1, le=6)
    folder_id: uuid.UUID | None = None
    preview_data: str | None = None
    discount_percent: int = Field(0, ge=0, le=100)
    min_discount_quantity: int = Field(1, ge=1)


class AdminCourseOut(BaseModel):
    id: uuid.UUID
    title: str
    description: str
    price_piastres: int
    price_egp: float
    category: str
    product_type: str
    medical_year: int = 1
    folder_id: uuid.UUID | None = None
    preview_data: str | None = None
    is_active: bool
    created_at: datetime


class AdminCurriculumItemOut(BaseModel):
    id: uuid.UUID
    course_id: uuid.UUID
    title: str
    content_type: str
    url_or_path: str
    duration_seconds: int = 0
    thumbnail_url: str | None = None
    created_at: datetime


class AdminCurriculumCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    content_type: str = Field("video", description="'video', 'link', or 'pdf'")
    url_or_path: str = Field(..., min_length=1)
    duration_seconds: int = Field(0, ge=0)


class AdminOptionIn(BaseModel):
    text: str = Field(..., min_length=1)
    is_correct: bool = Field(False)

    @model_validator(mode="before")
    @classmethod
    def _coerce_string(cls, data: Any) -> Any:
        if isinstance(data, str):
            return {"text": data.strip(), "is_correct": False}
        return data


class AdminQuestionIn(BaseModel):
    stem: str = Field(..., min_length=1)
    explanation: str = Field("", description="Clinical rationale")
    points: int = Field(1, ge=1)
    options: list[AdminOptionIn] = Field(..., min_length=2)
    correct_answer: str | int | None = Field(None, description="Optional indicator of correct answer (e.g. 'A', 'B', 0, 1, or text)")

    @model_validator(mode="before")
    @classmethod
    def _normalize_correct_answer(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if data.get("correct_answer") is None:
                for alias in (
                    "answer",
                    "correctAnswer",
                    "correct-answer",
                    "correct",
                    "correct_option",
                    "correctOption",
                    "correct_choice",
                    "correctChoice",
                    "answer_index",
                    "answerIndex",
                    "ans",
                    "key",
                    "solution",
                ):
                    if alias in data and data[alias] is not None:
                        data["correct_answer"] = data[alias]
                        break
        return data


class AdminQuizCreateRequest(BaseModel):
    title: str = Field(..., min_length=2, max_length=255)
    description: str = Field("", max_length=1000)
    category: str = Field("Medical Exam", max_length=64)
    medical_year: int = Field(1, ge=1, le=6)
    folder_id: uuid.UUID | None = None
    pass_percentage: int = Field(60, ge=0, le=100)
    time_limit_seconds: int | None = Field(None, ge=10)
    exam_mode: str = Field("PRACTICE", max_length=32)
    show_explanations: bool = True
    questions: list[AdminQuestionIn] = Field(..., min_length=1)


class AdminQuizOut(BaseModel):
    id: uuid.UUID
    title: str
    description: str
    category: str
    medical_year: int = 1
    folder_id: uuid.UUID | None = None
    pass_percentage: int
    time_limit_seconds: int | None = None
    is_active: bool = True
    exam_mode: str = "PRACTICE"
    show_explanations: bool = True
    questions_count: int
    attempts_count: int
    created_at: datetime


class AdminCardIn(BaseModel):
    front: str = Field(..., min_length=1)
    back: str = Field(..., min_length=1)
    hint: str | None = None
    tags: str | None = None


class AdminDeckCreateRequest(BaseModel):
    title: str = Field(..., min_length=2, max_length=255)
    description: str = Field("", max_length=1000)
    category: str = Field("Medical Flashcards", max_length=64)
    medical_year: int = Field(1, ge=1, le=6)
    folder_id: uuid.UUID | None = None
    cards: list[AdminCardIn] = Field(..., min_length=1)


class AdminDeckOut(BaseModel):
    id: uuid.UUID
    title: str
    description: str
    category: str
    medical_year: int = 1
    folder_id: uuid.UUID | None = None
    cards_count: int = 0
    total_cards: int | None = None
    is_active: bool = True
    created_at: datetime | None = None


class AdminPdfUploadOut(BaseModel):
    """Response returned after a real PDF is uploaded and persisted on the server."""

    id: uuid.UUID
    title: str
    description: str
    price_piastres: int
    price_egp: float
    category: str
    product_type: str
    medical_year: int = 1
    folder_id: uuid.UUID | None = None
    preview_data: str | None = None
    is_active: bool
    created_at: datetime
    asset_id: uuid.UUID
    stored_filename: str
    size_bytes: int


# ==============================================================================
# Admin Team & Role Management Schemas
# ==============================================================================


class AdminTeamMemberOut(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    phone: str | None = None
    role: str  # "SUPER_ADMIN" or "ADMIN"
    can_add_admins: bool
    roles: list[str]
    assigned_at: datetime
    is_active: bool


class AdminPromoteRequest(BaseModel):
    identifier: str = Field(..., min_length=2, max_length=255, description="User email, phone, or ID")
    role: str = Field("ADMIN", description="Target role: 'ADMIN', 'SUPER_ADMIN', or 'CREATOR'")


class AdminDemoteRequest(BaseModel):
    user_id: uuid.UUID


# ==============================================================================
# Course Update & Contact Info Schemas
# ==============================================================================


class AdminCourseUpdateRequest(BaseModel):
    title: str | None = Field(None, min_length=2, max_length=255)
    description: str | None = Field(None, max_length=2000)
    price_egp: float | None = Field(None, ge=0.0)
    category: str | None = Field(None, max_length=64)
    medical_year: int | None = Field(None, ge=1, le=6)
    folder_id: uuid.UUID | None = None
    preview_data: str | None = None
    is_active: bool | None = None


class AdminQuizUpdateRequest(BaseModel):
    title: str | None = Field(None, min_length=2, max_length=255)
    description: str | None = Field(None, max_length=2000)
    category: str | None = Field(None, max_length=64)
    medical_year: int | None = Field(None, ge=1, le=6)
    folder_id: uuid.UUID | None = None
    pass_percentage: int | None = Field(None, ge=0, le=100)
    time_limit_seconds: int | None = Field(None, ge=0)
    is_active: bool | None = None
    exam_mode: str | None = None
    show_explanations: bool | None = None


class StudentExamAttemptOut(BaseModel):
    attempt_id: uuid.UUID
    user_id: uuid.UUID
    student_name: str
    student_email: str
    student_phone: str | None = None
    medical_year: int = 1
    score: int
    max_score: int
    percentage: float
    passed: bool
    started_at: datetime
    completed_at: datetime
    time_spent_seconds: int = 0


class AdminQuizResultsSummaryOut(BaseModel):
    total_attempts: int
    total_students: int
    average_percentage: float
    highest_percentage: float
    lowest_percentage: float
    pass_rate_percentage: float
    pass_count: int
    fail_count: int


class AdminQuizResultsResponse(BaseModel):
    quiz_id: uuid.UUID
    quiz_title: str
    exam_mode: str
    pass_percentage: int
    time_limit_seconds: int | None = None
    summary: AdminQuizResultsSummaryOut
    attempts: list[StudentExamAttemptOut]


class AdminDeckUpdateRequest(BaseModel):
    title: str | None = Field(None, min_length=2, max_length=255)
    description: str | None = Field(None, max_length=2000)
    category: str | None = Field(None, max_length=64)
    medical_year: int | None = Field(None, ge=1, le=6)
    folder_id: uuid.UUID | None = None
    is_public: bool | None = None


class ContactInfoOut(BaseModel):
    vodafone_cash_number: str = "01004128527"
    telegram_bot_username: str = "@MedFighter_bot"
    telegram_bot_token: str = "8505734437:AAG9QSGJ87GtpW7qGngcAxZO6cWAHoE8g3w"
    admin_telegram_1: str = "@Mohamed_Hamed_Samaha"
    admin_telegram_2: str = "@Moh_gom3a"
    updated_at: datetime | None = None


class ContactInfoUpdate(BaseModel):
    vodafone_cash_number: str = Field(..., min_length=8, max_length=32)
    telegram_bot_username: str = Field(..., min_length=3, max_length=64)
    telegram_bot_token: str = Field(..., min_length=10, max_length=128)
    admin_telegram_1: str = Field(..., min_length=2, max_length=64)
    admin_telegram_2: str = Field(..., min_length=2, max_length=64)


# ==============================================================================
# Platform Security Settings Schemas
# ==============================================================================


class SecuritySettingsOut(BaseModel):
    allow_screenshots: bool = False
    updated_at: datetime | None = None


class SecuritySettingsUpdate(BaseModel):
    allow_screenshots: bool


class AdminAssignCreatorFolderRequest(BaseModel):
    folder_id: str | None = None



