"""Video domain service (§33, §34).

Enforces:
- Independent entitlement verification before issuing playback sessions.
- Device binding verification: active, registered devices only.
- Short-lived Vimeo playback tokens: master Vimeo credentials never reach client.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Forbidden, NotFound
from app.modules.audit.service import record_audit_log
from app.modules.entitlement.models import Entitlement
from app.modules.identity.models import Device
from app.modules.video.models import VideoAsset, VideoSession
from app.modules.video.schemas import VideoAssetOut, VideoSessionResponse

_JWT_SECRET = os.environ.get(
    "FIGHTERS_JWT_SECRET", "dev-jwt-secret-do-not-use-in-prod-32bytesmin!!"
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


async def list_product_videos(
    db: AsyncSession,
    product_id: uuid.UUID,
) -> list[VideoAssetOut]:
    """List video lectures belonging to a course or product."""
    query = (
        select(VideoAsset)
        .where(VideoAsset.product_id == product_id)
        .order_by(VideoAsset.created_at.asc())
    )
    assets = (await db.scalars(query)).all()
    return [VideoAssetOut.model_validate(a) for a in assets]


async def create_video_playback_session(
    db: AsyncSession,
    user_id: uuid.UUID,
    video_asset_id: uuid.UUID,
    device_id: uuid.UUID,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> VideoSessionResponse:
    """Issue short-lived Vimeo playback session after strict entitlement verification (§33, §34)."""
    # 1. Fetch Video Asset
    video = await db.scalar(select(VideoAsset).where(VideoAsset.id == video_asset_id))
    if not video:
        raise NotFound("VideoAsset")

    # 2. Verify Entitlement
    entitlement = await db.scalar(
        select(Entitlement).where(
            Entitlement.user_id == user_id,
            Entitlement.product_id == video.product_id,
            Entitlement.status == "ACTIVE",
        )
    )
    if not entitlement:
        raise Forbidden("You do not own an active entitlement for this video course.")

    # 3. Verify Device
    device = await db.scalar(
        select(Device).where(
            Device.id == device_id,
            Device.user_id == user_id,
        )
    )
    if not device:
        raise NotFound("Device")
    if device.status != "ACTIVE":
        raise Forbidden(f"Device status is '{device.status}'. Only active devices can play video.")

    now = _utc_now()
    expires_at = now + timedelta(hours=2)

    # 4. Generate signed short-lived playback token
    raw_payload = f"{user_id}:{video.id}:{device.id}:{int(expires_at.timestamp())}"
    token = hmac.new(_JWT_SECRET.encode(), raw_payload.encode(), hashlib.sha256).hexdigest()

    # Construct secure Vimeo embed URL with player parameters
    embed_url = (
        f"https://player.vimeo.com/video/{video.vimeo_video_id}"
        f"?token={token}&badge=0&autopause=0&player_id=fighters_player"
    )

    session = VideoSession(
        user_id=user_id,
        video_asset_id=video.id,
        device_id=device.id,
        playback_token=token,
        embed_url=embed_url,
        expires_at=expires_at,
        created_at=now,
    )
    db.add(session)
    await db.flush()

    await record_audit_log(
        db,
        action="video.playback_session_created",
        resource_type="video_session",
        actor_id=user_id,
        resource_id=str(session.id),
        details={
            "video_asset_id": str(video.id),
            "device_id": str(device.id),
            "expires_at": expires_at.isoformat(),
        },
        ip_address=ip_address,
        user_agent=user_agent,
    )

    await db.commit()
    await db.refresh(session)

    return VideoSessionResponse(
        id=session.id,
        video_asset_id=session.video_asset_id,
        title=video.title,
        embed_url=session.embed_url,
        playback_token=session.playback_token,
        expires_at=session.expires_at,
    )
