"""Entitlement and device licensing domain service (§13, §16, §17, §18).

Enforces:
- Content ownership verification prior to license issuance.
- Bound to registered, ACTIVE devices only.
- Cryptographically signed short-lived license tokens (§16).
- Wrapped Content Encryption Keys (CEK) bound to device key material (§17).
"""

from __future__ import annotations

import base64
import hashlib
import os
import uuid
from datetime import UTC, datetime, timedelta

import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Forbidden, NotFound
from app.modules.audit.service import record_audit_log
from app.modules.content.models import ContentAsset
from app.modules.entitlement.models import DeviceLicense, Entitlement
from app.modules.entitlement.schemas import EntitlementResponse, LicenseRequest, LicenseResponse
from app.modules.identity.models import Device

_JWT_SECRET = os.environ.get(
    "FIGHTERS_JWT_SECRET", "dev-jwt-secret-do-not-use-in-prod-32bytesmin!!"
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


async def list_user_entitlements(
    db: AsyncSession,
    user_id: uuid.UUID,
) -> list[EntitlementResponse]:
    """List all entitlements owned by the user."""
    query = (
        select(Entitlement)
        .where(Entitlement.user_id == user_id)
        .order_by(Entitlement.granted_at.desc())
    )
    results = (await db.scalars(query)).all()
    return [
        EntitlementResponse(
            id=e.id,
            user_id=e.user_id,
            product_id=e.product_id,
            status=e.status,
            granted_at=e.granted_at,
            expires_at=e.expires_at,
        )
        for e in results
    ]


async def issue_device_license(
    db: AsyncSession,
    user_id: uuid.UUID,
    entitlement_id: uuid.UUID,
    request: LicenseRequest,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> LicenseResponse:
    """Issue a device-bound short-lived cryptographic license (§16, §17, §18)."""
    # 1. Verify Entitlement
    entitlement = await db.scalar(
        select(Entitlement).where(
            Entitlement.id == entitlement_id,
            Entitlement.user_id == user_id,
        )
    )
    if not entitlement:
        raise NotFound("Entitlement")

    if entitlement.status != "ACTIVE":
        raise Forbidden(f"Entitlement is {entitlement.status}, not active.")

    now = _utc_now()
    if entitlement.expires_at:
        exp = entitlement.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=UTC)
        if exp < now:
            raise Forbidden("Entitlement has expired.")

    # 2. Verify Device
    device = await db.scalar(
        select(Device).where(
            Device.id == request.device_id,
            Device.user_id == user_id,
        )
    )
    if not device:
        raise NotFound("Device")

    if device.status != "ACTIVE":
        raise Forbidden(
            f"Device status is '{device.status}'. Only active devices can receive licenses."
        )

    # 3. Verify ContentAsset
    asset = await db.scalar(
        select(ContentAsset).where(
            ContentAsset.id == request.content_asset_id,
            ContentAsset.product_id == entitlement.product_id,
        )
    )
    if not asset:
        raise NotFound("ContentAsset")

    # 4. Generate Short-Lived License Token (§16)
    expires = now + timedelta(hours=24)
    license_claims = {
        "sub": str(user_id),
        "device_id": str(device.id),
        "entitlement_id": str(entitlement.id),
        "content_asset_id": str(asset.id),
        "iat": int(now.timestamp()),
        "exp": int(expires.timestamp()),
        "jti": str(uuid.uuid4()),
    }
    license_token = jwt.encode(license_claims, _JWT_SECRET, algorithm="HS256")

    # 5. Generate Wrapped CEK (§17)
    # Master CEK derived authoritatively from asset and master secret
    master_cek = hashlib.sha256(f"CEK:{asset.id}:{_JWT_SECRET}".encode()).digest()
    # Bound to device key material / fingerprint
    device_binding_key = hashlib.sha256(
        f"{device.id}:{device.device_fingerprint}:{device.public_key[:32]}".encode()
    ).digest()
    # XOR / mask CEK with device key (AES-KW proxy in baseline)
    wrapped_cek_bytes = bytes(a ^ b for a, b in zip(master_cek, device_binding_key, strict=True))
    wrapped_cek = base64.b64encode(wrapped_cek_bytes).decode("ascii")

    # 6. Save DeviceLicense record
    license_record = DeviceLicense(
        entitlement_id=entitlement.id,
        device_id=device.id,
        license_token=license_token,
        wrapped_cek=wrapped_cek,
        issued_at=now,
        expires_at=expires,
    )
    db.add(license_record)
    await db.flush()

    # 7. Audit log
    await record_audit_log(
        db,
        action="entitlement.license_issued",
        resource_type="device_license",
        actor_id=user_id,
        resource_id=str(license_record.id),
        details={
            "device_id": str(device.id),
            "content_asset_id": str(asset.id),
            "entitlement_id": str(entitlement.id),
            "expires_at": expires.isoformat(),
        },
        ip_address=ip_address,
        user_agent=user_agent,
    )

    await db.commit()
    await db.refresh(license_record)

    return LicenseResponse(
        id=license_record.id,
        entitlement_id=license_record.entitlement_id,
        device_id=license_record.device_id,
        license_token=license_record.license_token,
        wrapped_cek=license_record.wrapped_cek,
        issued_at=license_record.issued_at,
        expires_at=license_record.expires_at,
    )
