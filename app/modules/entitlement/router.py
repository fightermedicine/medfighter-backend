"""Entitlements and licensing API router (§13, §16, §17, §18)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Request

from app.common.deps import DbSession
from app.modules.entitlement.schemas import EntitlementResponse, LicenseRequest, LicenseResponse
from app.modules.entitlement.service import issue_device_license, list_user_entitlements
from app.modules.identity.deps import RequireUser

router = APIRouter(prefix="/entitlements", tags=["entitlements"])


@router.get("", response_model=list[EntitlementResponse])
async def get_entitlements(
    current_user: RequireUser,
    db: DbSession,
) -> list[EntitlementResponse]:
    """List all active entitlements for current authenticated user."""
    return await list_user_entitlements(db, current_user.id)


@router.post("/{entitlement_id}/license", response_model=LicenseResponse, status_code=201)
async def request_license(
    entitlement_id: uuid.UUID,
    payload: LicenseRequest,
    request: Request,
    current_user: RequireUser,
    db: DbSession,
) -> LicenseResponse:
    """Request a device-bound short-lived license token and wrapped CEK."""
    ip_address = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")
    return await issue_device_license(
        db,
        user_id=current_user.id,
        entitlement_id=entitlement_id,
        request=payload,
        ip_address=ip_address,
        user_agent=user_agent,
    )
