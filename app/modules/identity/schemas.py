"""Identity request/response schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserRegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str = Field(min_length=2, max_length=255)
    phone: str | None = Field(default=None, max_length=32)
    gender: str | None = Field(default="MALE")
    medical_year: int = Field(default=1, ge=1, le=6)


class VerifyEmailRequest(BaseModel):
    email: EmailStr
    code: str = Field(min_length=4, max_length=10)


class ResendCodeRequest(BaseModel):
    email: EmailStr


class GoogleAuthRequest(BaseModel):
    id_token: str
    device_fingerprint: str = Field(min_length=8, max_length=255)
    platform: str = Field(pattern="^(android|ios|windows|macos|linux|web)$")
    public_key: str = Field(min_length=16)
    email: EmailStr | None = None
    full_name: str | None = None
    medical_year: int | None = Field(default=1, ge=1, le=6)
    gender: str | None = None
    phone: str | None = None
    device_model: str | None = Field(default=None, max_length=128)
    os_version: str | None = Field(default=None, max_length=64)


class UserLoginRequest(BaseModel):
    email: EmailStr
    password: str
    device_fingerprint: str = Field(min_length=8, max_length=255)
    platform: str = Field(pattern="^(android|ios|windows|macos|linux|web)$")
    public_key: str = Field(min_length=16)
    device_model: str | None = Field(default=None, max_length=128)
    os_version: str | None = Field(default=None, max_length=64)


class RefreshTokenRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    expires_in: int
    user_id: uuid.UUID
    roles: list[str]


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    full_name: str
    phone: str | None
    gender: str | None = None
    medical_year: int = 1
    is_verified: bool = False
    is_active: bool
    roles: list[str]
    created_at: datetime


class DeviceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    device_fingerprint: str
    platform: str
    model: str | None
    os_version: str | None
    status: str
    registered_at: datetime
    last_seen_at: datetime


class UpdateProfileRequest(BaseModel):
    medical_year: int | None = Field(default=None, ge=1, le=6)
    full_name: str | None = Field(default=None, max_length=255)
    gender: str | None = Field(default=None, max_length=16)
    phone: str | None = Field(default=None, max_length=32)


