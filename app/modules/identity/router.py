"""Identity API router (§37, §38).

Exposes registration, authentication, token rotation, and device management.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.errors import ProblemError
from app.modules.audit.service import record_audit_log
from app.modules.identity.deps import RequireUser
from app.modules.identity.models import Device
from app.modules.identity.schemas import (
    DeviceResponse,
    GoogleAuthRequest,
    RefreshTokenRequest,
    ResendCodeRequest,
    TokenResponse,
    UpdateProfileRequest,
    UserLoginRequest,
    UserRegisterRequest,
    UserResponse,
    VerifyEmailRequest,
)
from app.modules.identity.service import (
    authenticate_google_user,
    authenticate_user,
    refresh_tokens,
    register_user,
    resend_verification_code,
    verify_email,
)

router = APIRouter(tags=["identity"])


def _extract_client_meta(request: Request) -> tuple[str | None, str | None]:
    ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent")
    return ip, ua


@router.post(
    "/auth/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new learner account",
)
async def register(
    request: Request,
    payload: UserRegisterRequest,
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    ip, ua = _extract_client_meta(request)
    user = await register_user(db, payload, ip_address=ip, user_agent=ua)
    role_names = [ur.role_id for ur in user.roles]
    return UserResponse(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        phone=user.phone,
        gender=user.gender,
        medical_year=user.medical_year,
        is_verified=user.is_verified,
        is_active=user.is_active,
        roles=role_names,
        created_at=user.created_at,
    )


@router.post(
    "/auth/verify-email",
    status_code=status.HTTP_200_OK,
    summary="Verify student email using 6-digit OTP code",
)
async def verify_student_email(
    payload: VerifyEmailRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    await verify_email(db, email=payload.email, code=payload.code)
    return {"success": True, "message": "Email verified successfully."}


@router.post(
    "/auth/resend-code",
    status_code=status.HTTP_200_OK,
    summary="Resend 6-digit verification code to student email",
)
async def resend_code(
    payload: ResendCodeRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    code = await resend_verification_code(db, email=payload.email)
    return {"success": True, "code": code, "message": "Verification code resent."}


@router.post(
    "/auth/google",
    response_model=TokenResponse,
    summary="Sign in or register with Google OAuth and issue hardware-bound tokens",
)
async def google_auth(
    request: Request,
    payload: GoogleAuthRequest,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    ip, ua = _extract_client_meta(request)
    try:
        return await authenticate_google_user(db, payload, ip_address=ip, user_agent=ua)
    except ProblemError:
        raise
    except Exception as exc:
        logger = get_logger("identity.router")
        logger.error("google_auth_unexpected_error", error=str(exc), exc_info=True)
        raise ProblemError(
            status_code=500,
            code="google_auth_failed",
            detail=f"Google authentication service error: {exc}",
        )


@router.post(
    "/auth/login",
    response_model=TokenResponse,
    summary="Authenticate credentials, bind device, and issue short-lived tokens",
)
async def login(
    request: Request,
    payload: UserLoginRequest,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    ip, ua = _extract_client_meta(request)
    return await authenticate_user(db, payload, ip_address=ip, user_agent=ua)


@router.post(
    "/auth/refresh",
    response_model=TokenResponse,
    summary="Rotate refresh token with reuse-detection family revocation",
)
async def refresh(
    request: Request,
    payload: RefreshTokenRequest,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    ip, ua = _extract_client_meta(request)
    return await refresh_tokens(db, payload.refresh_token, ip_address=ip, user_agent=ua)


@router.get(
    "/auth/me",
    response_model=UserResponse,
    summary="Fetch profile and server-authoritative roles of authenticated user",
)
async def get_current_profile(
    current_user: RequireUser,
) -> UserResponse:
    role_names = [ur.role_id for ur in current_user.roles]
    return UserResponse(
        id=current_user.id,
        email=current_user.email,
        full_name=current_user.full_name,
        phone=current_user.phone,
        gender=current_user.gender,
        medical_year=current_user.medical_year,
        is_verified=current_user.is_verified,
        is_active=current_user.is_active,
        roles=role_names,
        created_at=current_user.created_at,
    )


@router.patch(
    "/auth/profile",
    response_model=UserResponse,
    summary="Update authenticated user profile attributes (academic year, name, gender, phone)",
)
async def update_profile(
    payload: UpdateProfileRequest,
    current_user: RequireUser,
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    if payload.medical_year is not None:
        current_user.medical_year = payload.medical_year
    if payload.full_name is not None and payload.full_name.strip():
        current_user.full_name = payload.full_name.strip()
    if payload.gender is not None:
        current_user.gender = payload.gender
    if payload.phone is not None:
        current_user.phone = payload.phone.strip()

    await db.commit()
    await db.refresh(current_user, ["roles"])
    role_names = [ur.role_id for ur in current_user.roles]
    return UserResponse(
        id=current_user.id,
        email=current_user.email,
        full_name=current_user.full_name,
        phone=current_user.phone,
        gender=current_user.gender,
        medical_year=current_user.medical_year,
        is_verified=current_user.is_verified,
        is_active=current_user.is_active,
        roles=role_names,
        created_at=current_user.created_at,
    )


@router.get(
    "/auth/devices",
    response_model=list[DeviceResponse],
    summary="List all registered devices bound to authenticated user",
)
async def list_devices(
    current_user: RequireUser,
    db: AsyncSession = Depends(get_db),
) -> list[DeviceResponse]:
    query = select(Device).where(Device.user_id == current_user.id)
    devices = (await db.scalars(query)).all()
    return [
        DeviceResponse(
            id=d.id,
            device_fingerprint=d.device_fingerprint,
            platform=d.platform,
            model=d.model,
            os_version=d.os_version,
            status=d.status,
            registered_at=d.registered_at,
            last_seen_at=d.last_seen_at,
        )
        for d in devices
    ]


@router.delete(
    "/auth/devices/{device_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Revoke an authorized device binding",
)
async def revoke_device(
    device_id: uuid.UUID,
    request: Request,
    current_user: RequireUser,
    db: AsyncSession = Depends(get_db),
) -> Response:
    query = select(Device).where(Device.id == device_id, Device.user_id == current_user.id)
    device = await db.scalar(query)
    if not device:
        raise ProblemError(
            status_code=404,
            code="not_found",
            detail="The specified device was not found.",
        )

    device.status = "REVOKED"
    ip, ua = _extract_client_meta(request)
    await record_audit_log(
        db,
        action="identity.device_revoked",
        resource_type="device",
        actor_id=current_user.id,
        resource_id=str(device.id),
        details={"fingerprint": device.device_fingerprint},
        ip_address=ip,
        user_agent=ua,
    )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
