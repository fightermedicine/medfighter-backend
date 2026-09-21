"""Notifications domain service."""

from __future__ import annotations

import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFound
from app.modules.notifications.models import Notification, UserNotificationRead
from app.modules.notifications.schemas import NotificationOut


async def create_notification(
    db: AsyncSession,
    title: str,
    message: str,
    notification_type: str = "ANNOUNCEMENT",
    target_medical_year: int = 0,
    resource_type: str | None = None,
    resource_id: str | None = None,
    created_by: uuid.UUID | None = None,
) -> Notification:
    """Create a notification in the persistent ledger."""
    notification = Notification(
        title=title,
        message=message,
        notification_type=notification_type,
        target_medical_year=target_medical_year,
        resource_type=resource_type,
        resource_id=resource_id,
        created_by=created_by,
    )
    db.add(notification)
    await db.commit()
    await db.refresh(notification)
    return notification


async def list_user_notifications(
    db: AsyncSession,
    user_id: uuid.UUID,
    medical_year: int,
    limit: int = 50,
) -> list[NotificationOut]:
    """List notifications relevant to the user (targeted to their medical year or all years)."""
    # Fetch notifications matching target_medical_year == 0 OR target_medical_year == medical_year
    stmt = (
        select(Notification)
        .where(
            or_(
                Notification.target_medical_year == 0,
                Notification.target_medical_year == medical_year,
            )
        )
        .order_by(Notification.created_at.desc())
        .limit(limit)
    )
    notifications = (await db.scalars(stmt)).all()

    # Get user's read notification IDs
    notif_ids = [n.id for n in notifications]
    read_ids: set[uuid.UUID] = set()
    if notif_ids:
        read_stmt = select(UserNotificationRead.notification_id).where(
            UserNotificationRead.user_id == user_id,
            UserNotificationRead.notification_id.in_(notif_ids),
        )
        read_ids = set((await db.scalars(read_stmt)).all())

    return [
        NotificationOut(
            id=n.id,
            title=n.title,
            message=n.message,
            notification_type=n.notification_type,
            target_medical_year=n.target_medical_year,
            resource_type=n.resource_type,
            resource_id=n.resource_id,
            created_at=n.created_at,
            is_read=(n.id in read_ids),
        )
        for n in notifications
    ]


async def get_unread_count(
    db: AsyncSession,
    user_id: uuid.UUID,
    medical_year: int,
) -> int:
    """Count unread notifications for student."""
    # Count total notifications matching year
    total_stmt = select(func.count()).select_from(Notification).where(
        or_(
            Notification.target_medical_year == 0,
            Notification.target_medical_year == medical_year,
        )
    )
    total_count = (await db.scalar(total_stmt)) or 0

    # Count read notifications matching year
    read_stmt = (
        select(func.count())
        .select_from(UserNotificationRead)
        .join(Notification, UserNotificationRead.notification_id == Notification.id)
        .where(
            UserNotificationRead.user_id == user_id,
            or_(
                Notification.target_medical_year == 0,
                Notification.target_medical_year == medical_year,
            ),
        )
    )
    read_count = (await db.scalar(read_stmt)) or 0

    return max(0, total_count - read_count)


async def mark_notification_read(
    db: AsyncSession,
    user_id: uuid.UUID,
    notification_id: uuid.UUID,
) -> None:
    """Mark a notification as read by user."""
    notif = await db.scalar(select(Notification).where(Notification.id == notification_id))
    if not notif:
        raise NotFound("Notification")

    # Check if already marked
    existing = await db.scalar(
        select(UserNotificationRead).where(
            UserNotificationRead.user_id == user_id,
            UserNotificationRead.notification_id == notification_id,
        )
    )
    if not existing:
        db.add(UserNotificationRead(user_id=user_id, notification_id=notification_id))
        await db.commit()


async def mark_all_read(
    db: AsyncSession,
    user_id: uuid.UUID,
    medical_year: int,
) -> None:
    """Mark all matching notifications as read."""
    stmt = select(Notification.id).where(
        or_(
            Notification.target_medical_year == 0,
            Notification.target_medical_year == medical_year,
        )
    )
    notif_ids = (await db.scalars(stmt)).all()

    for nid in notif_ids:
        existing = await db.scalar(
            select(UserNotificationRead).where(
                UserNotificationRead.user_id == user_id,
                UserNotificationRead.notification_id == nid,
            )
        )
        if not existing:
            db.add(UserNotificationRead(user_id=user_id, notification_id=nid))
    await db.commit()


# ── Payment notification helpers ──────────────────────────────────────────────

async def _notify_user(
    db: AsyncSession,
    user_id: uuid.UUID,
    title: str,
    message: str,
    notification_type: str = "PAYMENT",
    resource_type: str = "payment",
    resource_id: str | None = None,
) -> None:
    """Create a targeted notification for a single user and mark it as pending read."""
    notif = Notification(
        title=title,
        message=message,
        notification_type=notification_type,
        target_medical_year=0,  # 0 = all years / targeted
        resource_type=resource_type,
        resource_id=resource_id,
    )
    db.add(notif)
    await db.flush()
    # Mark as needing delivery to this specific user by NOT pre-marking read
    # The client polls /notifications and sees unread entries
    await db.commit()


async def notify_admins_payment_pending(db: AsyncSession, payment: object) -> None:
    """Notify all admin users of a new payment awaiting review."""
    from app.modules.identity.models import User, UserRole
    admin_user_ids = (await db.scalars(
        select(UserRole.user_id).where(UserRole.role_id.in_(["ADMIN", "SUPER_ADMIN"]))
    )).all()
    admins = (await db.scalars(
        select(User).where(User.id.in_(admin_user_ids), User.is_active.is_(True))
    )).all()
    for admin in admins:
        await _notify_user(
            db,
            user_id=admin.id,
            title="💰 New Payment Pending",
            message=f"Payment {getattr(payment, 'reference', '')} — {getattr(payment, 'amount_piastres', 0) / 100:.0f} EGP — needs review.",
            notification_type="ADMIN_PAYMENT",
            resource_id=str(getattr(payment, 'id', '')),
        )

    # Telegram push to all admin channels (with photo if available)
    try:
        from pathlib import Path
        from app.modules.catalog.models import Product
        from app.modules.telegram.service import broadcast_photo_to_admins, broadcast_to_admins

        ref = getattr(payment, "reference", "")
        egp = getattr(payment, "amount_piastres", 0) / 100
        proof_path = getattr(payment, "proof_path", None)

        user_id = getattr(payment, "user_id", None)
        user = await db.get(User, user_id) if user_id else None
        user_name = user.full_name if user else "Doctor"
        user_email = user.email if user else ""

        product_id = getattr(payment, "product_id", None)
        product = await db.get(Product, product_id) if product_id else None
        product_title = product.title if product else "Medical Package"

        caption = (
            f"<b>Payment Submission</b>\n\n"
            f"Student: <b>{user_name}</b> ({user_email})\n"
            f"Product: {product_title}\n"
            f"Amount: <b>{egp:.0f} EGP</b>\n"
            f"Reference: <code>{ref}</code>"
        )

        photo_bytes = None
        if proof_path and Path(proof_path).exists():
            photo_bytes = Path(proof_path).read_bytes()

        from app.modules.telegram.service import build_payment_keyboard
        keyboard = build_payment_keyboard(ref)

        if photo_bytes:
            await broadcast_photo_to_admins(db, photo=photo_bytes, caption=caption, reply_markup=keyboard)
        else:
            await broadcast_to_admins(db, caption, reply_markup=keyboard)
    except Exception:  # noqa: BLE001
        pass


async def notify_payment_approved(db: AsyncSession, payment: object, product: object | None) -> None:
    """Notify user their payment was approved and content is available."""
    product_title = getattr(product, 'title', 'your product') if product else 'your product'
    await _notify_user(
        db,
        user_id=getattr(payment, 'user_id'),
        title="✅ Payment Approved",
        message=f"Your payment for '{product_title}' has been confirmed. Open My Library to access your content.",
        notification_type="PAYMENT",
        resource_id=str(getattr(payment, 'id', '')),
    )
    # Telegram DM to student
    try:
        from app.modules.telegram.service import notify_student
        await notify_student(
            db,
            getattr(payment, 'user_id'),
            f"✅ <b>Payment Approved!</b>\n\n"
            f"Your payment for <b>{product_title}</b> has been confirmed.\n"
            f"Open <b>My Library</b> in the MedFighter app to access your content.\n\n"
            f"Ref: <code>{getattr(payment, 'reference', '')}</code>",
        )
    except Exception:  # noqa: BLE001
        pass


async def notify_payment_rejected(
    db: AsyncSession, payment: object, product: object | None, reason: str
) -> None:
    """Notify user their payment was rejected with a reason."""
    product_title = getattr(product, 'title', 'your product') if product else 'your product'
    await _notify_user(
        db,
        user_id=getattr(payment, 'user_id'),
        title="❌ Payment Could Not Be Verified",
        message=f"Payment for '{product_title}' was rejected.\n\nReason: {reason}\n\nRef: {getattr(payment, 'reference', '')}",
        notification_type="PAYMENT",
        resource_id=str(getattr(payment, 'id', '')),
    )
    # Telegram DM to student
    try:
        from app.modules.telegram.service import notify_student
        await notify_student(
            db,
            getattr(payment, 'user_id'),
            f"❌ <b>Payment Could Not Be Verified</b>\n\n"
            f"Product: {product_title}\n"
            f"Reason: {reason}\n\n"
            f"Ref: <code>{getattr(payment, 'reference', '')}</code>\n\n"
            "Please re-submit with a clearer receipt or contact an admin.",
        )
    except Exception:  # noqa: BLE001
        pass


async def notify_payment_more_proof(
    db: AsyncSession, payment: object, product: object | None
) -> None:
    """Notify user to re-upload clearer payment proof."""
    product_title = getattr(product, 'title', 'your product') if product else 'your product'
    await _notify_user(
        db,
        user_id=getattr(payment, 'user_id'),
        title="📎 Clearer Proof Required",
        message=f"Please upload a clearer payment screenshot for '{product_title}'.\n\nRef: {getattr(payment, 'reference', '')}",
        notification_type="PAYMENT",
        resource_id=str(getattr(payment, 'id', '')),
    )
    # Telegram DM to student
    try:
        from app.modules.telegram.service import notify_student
        await notify_student(
            db,
            getattr(payment, 'user_id'),
            f"📎 <b>Clearer Receipt Required</b>\n\n"
            f"We couldn't verify your payment for <b>{product_title}</b>.\n\n"
            "Please send a clearer screenshot of your Vodafone Cash transfer here.\n\n"
            f"Ref: <code>{getattr(payment, 'reference', '')}</code>",
        )
    except Exception:  # noqa: BLE001
        pass
