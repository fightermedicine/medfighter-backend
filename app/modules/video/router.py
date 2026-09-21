"""Video module API router (§33, §34)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Request

from app.common.deps import DbSession
from app.modules.identity.deps import RequireUser
from app.modules.video.schemas import VideoAssetOut, VideoSessionRequest, VideoSessionResponse
from app.modules.video.service import create_video_playback_session, list_product_videos

router = APIRouter(prefix="/video", tags=["video"])


def _extract_client_meta(request: Request) -> tuple[str | None, str | None]:
    ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent")
    return ip, ua


@router.get("/products/{product_id}/videos", response_model=list[VideoAssetOut])
async def get_product_videos(
    product_id: uuid.UUID,
    db: DbSession,
    current_user: RequireUser,
) -> list[VideoAssetOut]:
    """List videos associated with a course or product."""
    return await list_product_videos(db, product_id)


@router.post("/videos/{asset_id}/session", response_model=VideoSessionResponse)
async def get_playback_session(
    asset_id: uuid.UUID,
    payload: VideoSessionRequest,
    request: Request,
    db: DbSession,
    current_user: RequireUser,
) -> VideoSessionResponse:
    """Issue authenticated, device-bound Vimeo playback session token."""
    ip, ua = _extract_client_meta(request)
    return await create_video_playback_session(
        db,
        user_id=current_user.id,
        video_asset_id=asset_id,
        device_id=payload.device_id,
        ip_address=ip,
        user_agent=ua,
    )
