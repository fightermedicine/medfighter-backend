"""Notifications HTTP router."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.modules.identity.deps import RequireAdmin, RequireUser
from app.modules.notifications.schemas import BroadcastNotificationRequest, NotificationOut
from app.modules.notifications.service import (
    create_notification,
    get_unread_count,
    list_user_notifications,
    mark_all_read,
    mark_notification_read,
)

router = APIRouter(prefix="", tags=["notifications"])


@router.get(
    "/v1/notifications/me",
    response_model=list[NotificationOut],
    status_code=status.HTTP_200_OK,
    summary="List student notifications filtered by medical year",
)
async def get_my_notifications(
    user: RequireUser,
    db: AsyncSession = Depends(get_db),
) -> list[NotificationOut]:
    med_year = getattr(user, "medical_year", 1) or 1
    return await list_user_notifications(db, user_id=user.id, medical_year=med_year)


@router.get(
    "/v1/notifications/unread-count",
    status_code=status.HTTP_200_OK,
    summary="Get count of unread notifications for current student",
)
async def get_my_unread_count(
    user: RequireUser,
    db: AsyncSession = Depends(get_db),
) -> dict[str, int]:
    med_year = getattr(user, "medical_year", 1) or 1
    count = await get_unread_count(db, user_id=user.id, medical_year=med_year)
    return {"unread_count": count}


@router.post(
    "/v1/notifications/{notification_id}/read",
    status_code=status.HTTP_200_OK,
    summary="Mark single notification as read",
)
async def mark_read(
    notification_id: uuid.UUID,
    user: RequireUser,
    db: AsyncSession = Depends(get_db),
) -> dict[str, bool]:
    await mark_notification_read(db, user_id=user.id, notification_id=notification_id)
    return {"success": True}


@router.post(
    "/v1/notifications/read-all",
    status_code=status.HTTP_200_OK,
    summary="Mark all student notifications as read",
)
async def mark_all_as_read(
    user: RequireUser,
    db: AsyncSession = Depends(get_db),
) -> dict[str, bool]:
    med_year = getattr(user, "medical_year", 1) or 1
    await mark_all_read(db, user_id=user.id, medical_year=med_year)
    return {"success": True}


@router.post(
    "/v1/notifications/broadcast",
    response_model=NotificationOut,
    status_code=status.HTTP_201_CREATED,
    summary="Admin broadcast custom announcement to all students or specific medical year (Admin only)",
)
async def admin_broadcast_notification(
    payload: BroadcastNotificationRequest,
    admin: RequireAdmin,
    db: AsyncSession = Depends(get_db),
) -> NotificationOut:
    notif = await create_notification(
        db=db,
        title=payload.title,
        message=payload.message,
        notification_type=payload.notification_type,
        target_medical_year=payload.target_medical_year,
        resource_type=payload.resource_type,
        resource_id=payload.resource_id,
        created_by=admin.id,
    )
    return NotificationOut(
        id=notif.id,
        title=notif.title,
        message=notif.message,
        notification_type=notif.notification_type,
        target_medical_year=notif.target_medical_year,
        resource_type=notif.resource_type,
        resource_id=notif.resource_id,
        created_at=notif.created_at,
        is_read=False,
    )
