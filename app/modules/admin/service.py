"""Admin domain service (§38–§40).

Server-authoritative control plane for:
- Platform KPI analytics.
- Student search & direct wallet top-up via double-entry ledger.
- Device roster & instant hardware unbinding.
- Forensic leak emergency lockdown.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import UTC, datetime

logger = logging.getLogger(__name__)


from sqlalchemy import delete, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.core.errors import NotFound, ProblemError
from app.core.money import egp_to_piastres, format_egp, piastres_to_egp
from app.modules.admin.models import PlatformSetting
from app.modules.admin.schemas import (
    AdminCourseUpdateRequest,
    AdminDeckUpdateRequest,
    AdminQuizUpdateRequest,
    ContactInfoUpdate,
    SecuritySettingsUpdate,
)
from app.modules.audit.service import record_audit_log
from app.modules.catalog.models import Bundle, BundleItem, PriceRule, Product, ProductVersion
from app.modules.content.models import ContentAsset
from app.modules.curriculum.models import CurriculumFolder
from app.modules.entitlement.models import DeviceLicense, Entitlement
from app.modules.identity.models import Device, Role, User, UserRole
from app.modules.identity.models import Session as UserSession
from app.modules.orders.models import Order, PurchaseUnit
from app.modules.payments.models import Payment
from app.modules.learning.models import (
    Card,
    Deck,
    Question,
    QuestionBank,
    QuestionOption,
    QuizAttempt,
)
from app.modules.video.models import VideoAsset, VideoSession
from app.modules.vouchers.models import Voucher
from app.modules.wallet.models import Deposit, LedgerEntry, LedgerTransaction, WalletAccount
from app.modules.wallet.service import (
    calculate_wallet_balance,
    get_or_create_system_clearing_account,
    get_or_create_wallet,
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


async def get_admin_stats(db: AsyncSession) -> dict:
    """Aggregate high-level platform KPI metrics for admin dashboard in a single round-trip."""
    stmt = select(
        select(func.count(User.id)).scalar_subquery(),
        select(
            func.coalesce(func.sum(LedgerEntry.amount_piastres), 0)
        )
        .select_from(LedgerEntry)
        .join(LedgerTransaction, LedgerEntry.transaction_id == LedgerTransaction.id)
        .where(
            LedgerTransaction.reference.startswith("ORD-"),
            LedgerEntry.direction == "CREDIT",
            LedgerTransaction.status == "POSTED",
        )
        .scalar_subquery(),
        select(func.count(Deposit.id)).where(Deposit.status == "PENDING").scalar_subquery(),
        select(func.count(Product.id)).where(Product.is_active.is_(True)).scalar_subquery(),
        select(func.count(Voucher.id)).where(Voucher.is_active.is_(True)).scalar_subquery(),
    )
    row = (await db.execute(stmt)).first()
    total_students = (row[0] or 0) if row else 0
    revenue_credits = (row[1] or 0) if row else 0
    pending_deposits = (row[2] or 0) if row else 0
    active_courses = (row[3] or 0) if row else 0
    active_vouchers = (row[4] or 0) if row else 0

    return {
        "total_students": total_students,
        "total_revenue_egp": piastres_to_egp(revenue_credits),
        "pending_deposits_count": pending_deposits,
        "active_courses_count": active_courses,
        "active_vouchers_count": active_vouchers,
    }


async def search_students(
    db: AsyncSession,
    query: str | None = None,
    medical_year: int | None = None,
    is_active: bool | None = None,
    limit: int = 100,
) -> list[dict]:
    """Search registered doctors/students with batch queries (O(1) round trips instead of O(N))."""
    stmt = select(User).options(selectinload(User.roles))
    if query and query.strip():
        q = f"%{query.strip()}%"
        stmt = stmt.where(
            or_(
                User.full_name.ilike(q),
                User.email.ilike(q),
                User.phone.ilike(q),
            )
        )
    if medical_year is not None and medical_year > 0:
        stmt = stmt.where(User.medical_year == medical_year)
    if is_active is not None:
        stmt = stmt.where(User.is_active == is_active)

    stmt = stmt.order_by(User.created_at.desc()).limit(limit)
    users = (await db.scalars(stmt)).all()
    if not users:
        return []

    user_ids = [u.id for u in users]

    # 1. Batch fetch or initialize wallets
    wallets = (
        await db.scalars(
            select(WalletAccount).where(
                WalletAccount.user_id.in_(user_ids),
                WalletAccount.currency == "EGP",
            )
        )
    ).all()
    wallet_map: dict[uuid.UUID, WalletAccount] = {w.user_id: w for w in wallets if w.user_id}
    missing_wallet_users = [u for u in users if u.id not in wallet_map]
    for u in missing_wallet_users:
        w = WalletAccount(user_id=u.id, account_type="USER_WALLET", currency="EGP")
        db.add(w)
        wallet_map[u.id] = w
    if missing_wallet_users:
        await db.flush()

    # 2. Batch calculate balances
    account_ids = [w.id for w in wallet_map.values()]
    balance_map: dict[uuid.UUID, int] = {acc_id: 0 for acc_id in account_ids}
    if account_ids:
        credits_res = await db.execute(
            select(LedgerEntry.account_id, func.coalesce(func.sum(LedgerEntry.amount_piastres), 0))
            .join(LedgerTransaction, LedgerEntry.transaction_id == LedgerTransaction.id)
            .where(
                LedgerEntry.account_id.in_(account_ids),
                LedgerEntry.direction == "CREDIT",
                LedgerTransaction.status == "POSTED",
            )
            .group_by(LedgerEntry.account_id)
        )
        debits_res = await db.execute(
            select(LedgerEntry.account_id, func.coalesce(func.sum(LedgerEntry.amount_piastres), 0))
            .join(LedgerTransaction, LedgerEntry.transaction_id == LedgerTransaction.id)
            .where(
                LedgerEntry.account_id.in_(account_ids),
                LedgerEntry.direction == "DEBIT",
                LedgerTransaction.status == "POSTED",
            )
            .group_by(LedgerEntry.account_id)
        )
        credits_dict = {row[0]: row[1] for row in credits_res.all()}
        debits_dict = {row[0]: row[1] for row in debits_res.all()}
        for acc_id in account_ids:
            balance_map[acc_id] = credits_dict.get(acc_id, 0) - debits_dict.get(acc_id, 0)

    # 3. Batch fetch all devices for matching users
    all_devices = (
        await db.scalars(
            select(Device)
            .where(Device.user_id.in_(user_ids))
            .order_by(Device.last_seen_at.desc())
        )
    ).all()
    devices_by_user: dict[uuid.UUID, list[dict]] = {uid: [] for uid in user_ids}
    for d in all_devices:
        devices_by_user.setdefault(d.user_id, []).append(
            {
                "id": d.id,
                "device_fingerprint": d.device_fingerprint,
                "platform": d.platform,
                "model": d.model,
                "status": d.status,
                "registered_at": d.registered_at,
                "last_seen_at": d.last_seen_at,
            }
        )

    # 4. Batch fetch all entitlements for matching users
    all_ent_rows = (
        await db.execute(
            select(Entitlement, Product.title)
            .join(Product, Entitlement.product_id == Product.id)
            .where(Entitlement.user_id.in_(user_ids))
            .order_by(Entitlement.granted_at.desc())
        )
    ).all()
    entitlements_by_user: dict[uuid.UUID, list[dict]] = {uid: [] for uid in user_ids}
    for ent, prod_title in all_ent_rows:
        entitlements_by_user.setdefault(ent.user_id, []).append(
            {
                "id": ent.id,
                "product_id": ent.product_id,
                "product_title": prod_title,
                "status": ent.status,
                "granted_at": ent.granted_at,
                "expires_at": ent.expires_at,
            }
        )

    results = []
    for u in users:
        w = wallet_map.get(u.id)
        bal = balance_map.get(w.id, 0) if w else 0
        user_devs = devices_by_user.get(u.id, [])
        active_dev_count = sum(1 for d in user_devs if d["status"] == "ACTIVE")
        user_ents = entitlements_by_user.get(u.id, [])
        roles = [ur.role_id for ur in u.roles] if u.roles else []

        results.append(
            {
                "id": u.id,
                "full_name": u.full_name,
                "email": u.email,
                "phone": u.phone,
                "gender": u.gender,
                "medical_year": u.medical_year,
                "is_verified": u.is_verified,
                "roles": roles,
                "wallet_balance_egp": piastres_to_egp(int(bal)),
                "devices_count": active_dev_count,
                "entitlements_count": len(user_ents),
                "is_active": u.is_active,
                "created_at": u.created_at,
                "devices": user_devs,
                "entitlements": user_ents,
            }
        )
    return results


async def set_user_status(
    db: AsyncSession,
    admin_id: uuid.UUID,
    user_id: uuid.UUID,
    is_active: bool,
    reason: str = "Admin status update",
) -> dict:
    """Ban/suspend or reactivate user account, wiping sessions immediately on ban."""
    user = await db.scalar(select(User).where(User.id == user_id))
    if not user:
        raise NotFound(f"User {user_id}")

    user.is_active = is_active
    now = _utc_now()

    if not is_active:
        # Revoke all active sessions so user is logged out immediately
        sessions = (
            await db.scalars(
                select(UserSession).where(
                    UserSession.user_id == user.id, UserSession.is_revoked.is_(False)
                )
            )
        ).all()
        for s in sessions:
            s.is_revoked = True

        # Also revoke active devices
        devices = (
            await db.scalars(
                select(Device).where(Device.user_id == user.id, Device.status == "ACTIVE")
            )
        ).all()
        for d in devices:
            d.status = "REVOKED"
            d.last_seen_at = now
    else:
        # Reactivate devices for the user
        devices = (await db.scalars(select(Device).where(Device.user_id == user.id))).all()
        for d in devices:
            d.status = "ACTIVE"

    await record_audit_log(
        db,
        action="admin.user_status_updated",
        resource_type="user",
        actor_id=admin_id,
        actor_role="ADMIN",
        resource_id=str(user.id),
        details={"is_active": is_active, "reason": reason},
    )
    await db.commit()
    return {"success": True, "user_id": str(user.id), "is_active": user.is_active}


async def revoke_all_user_devices(
    db: AsyncSession,
    admin_id: uuid.UUID,
    user_id: uuid.UUID,
) -> dict:
    """Revoke all registered hardware devices for a student to reset their slots."""
    user = await db.scalar(select(User).where(User.id == user_id))
    if not user:
        raise NotFound(f"User {user_id}")

    devices = (await db.scalars(select(Device).where(Device.user_id == user_id))).all()
    now = _utc_now()
    revoked_count = 0
    for d in devices:
        if d.status != "REVOKED":
            d.status = "REVOKED"
            d.last_seen_at = now
            revoked_count += 1

    await record_audit_log(
        db,
        action="admin.user_devices_revoked_all",
        resource_type="user",
        actor_id=admin_id,
        actor_role="ADMIN",
        resource_id=str(user.id),
        details={"revoked_count": revoked_count},
    )
    await db.commit()
    return {"success": True, "revoked_count": revoked_count}


async def list_user_entitlements_admin(
    db: AsyncSession,
    user_id: uuid.UUID,
) -> list[dict]:
    """List all course and product entitlements owned by a user."""
    query = (
        select(Entitlement, Product.title)
        .join(Product, Entitlement.product_id == Product.id)
        .where(Entitlement.user_id == user_id)
        .order_by(Entitlement.granted_at.desc())
    )
    rows = (await db.execute(query)).all()
    return [
        {
            "id": ent.id,
            "product_id": ent.product_id,
            "product_title": prod_title,
            "status": ent.status,
            "granted_at": ent.granted_at,
            "expires_at": ent.expires_at,
        }
        for ent, prod_title in rows
    ]


async def grant_user_entitlement_admin(
    db: AsyncSession,
    admin_id: uuid.UUID,
    user_id: uuid.UUID,
    product_id: uuid.UUID,
    reason: str = "Admin manual grant",
    expires_at: datetime | None = None,
) -> dict:
    """Grant student access to a course or educational product manually."""
    user = await db.scalar(select(User).where(User.id == user_id))
    if not user:
        raise NotFound(f"User {user_id}")
    product = await db.scalar(select(Product).where(Product.id == product_id))
    if not product:
        raise NotFound(f"Product {product_id}")

    existing = await db.scalar(
        select(Entitlement).where(
            Entitlement.user_id == user_id, Entitlement.product_id == product_id
        )
    )
    if existing:
        existing.status = "ACTIVE"
        if expires_at:
            existing.expires_at = expires_at
        ent = existing
    else:
        ent = Entitlement(
            user_id=user_id,
            product_id=product_id,
            status="ACTIVE",
            expires_at=expires_at,
        )
        db.add(ent)
    await db.flush()

    await record_audit_log(
        db,
        action="admin.entitlement_granted",
        resource_type="entitlement",
        actor_id=admin_id,
        actor_role="ADMIN",
        resource_id=str(ent.id),
        details={
            "user_id": str(user_id),
            "product_id": str(product_id),
            "product_title": product.title,
            "reason": reason,
        },
    )
    await db.commit()
    return {
        "id": ent.id,
        "product_id": ent.product_id,
        "product_title": product.title,
        "status": ent.status,
        "granted_at": ent.granted_at,
        "expires_at": ent.expires_at,
    }


async def revoke_user_entitlement_admin(
    db: AsyncSession,
    admin_id: uuid.UUID,
    user_id: uuid.UUID,
    entitlement_id: uuid.UUID,
) -> dict:
    """Revoke student access to a course or educational product."""
    ent = await db.scalar(
        select(Entitlement).where(
            Entitlement.id == entitlement_id, Entitlement.user_id == user_id
        )
    )
    if not ent:
        raise NotFound(f"Entitlement {entitlement_id}")

    ent.status = "REVOKED"
    await record_audit_log(
        db,
        action="admin.entitlement_revoked",
        resource_type="entitlement",
        actor_id=admin_id,
        actor_role="ADMIN",
        resource_id=str(ent.id),
        details={"user_id": str(user_id), "product_id": str(ent.product_id)},
    )
    await db.commit()
    return {"success": True, "message": f"Entitlement {entitlement_id} revoked."}


async def admin_topup_user(
    db: AsyncSession,
    admin_id: uuid.UUID,
    user_id: uuid.UUID,
    amount_egp: float,
    reason: str,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> dict:
    """Directly credit a student account using double-entry balanced ledger transaction."""
    user = await db.scalar(select(User).where(User.id == user_id))
    if not user:
        raise NotFound(f"User {user_id}")

    amount_piastres = egp_to_piastres(amount_egp)
    now = _utc_now()

    user_wallet = await get_or_create_wallet(db, user_id)
    clearing_account = await get_or_create_system_clearing_account(db)

    tx = LedgerTransaction(
        idempotency_key=f"admin-topup-{user_id}-{now.timestamp()}-{uuid.uuid4().hex[:6]}",
        reference=f"TOPUP-{uuid.uuid4().hex[:8].upper()}",
        description=f"Admin Top-Up: {reason}",
        status="POSTED",
        created_at=now,
        posted_at=now,
    )
    db.add(tx)
    await db.flush()

    debit_entry = LedgerEntry(
        transaction_id=tx.id,
        account_id=clearing_account.id,
        direction="DEBIT",
        amount_piastres=amount_piastres,
        created_at=now,
    )
    credit_entry = LedgerEntry(
        transaction_id=tx.id,
        account_id=user_wallet.id,
        direction="CREDIT",
        amount_piastres=amount_piastres,
        created_at=now,
    )
    db.add(debit_entry)
    db.add(credit_entry)
    await db.commit()

    new_bal_piastres = await calculate_wallet_balance(db, user_wallet.id)

    await record_audit_log(
        db,
        action="ADMIN_WALLET_TOPUP",
        resource_type="WalletAccount",
        resource_id=str(user_wallet.id),
        actor_id=admin_id,
        actor_role="ADMIN",
        ip_address=ip_address,
        user_agent=user_agent,
        details={
            "target_user_id": str(user_id),
            "amount_piastres": amount_piastres,
            "amount_egp": amount_egp,
            "reason": reason,
            "transaction_id": str(tx.id),
        },
    )

    return {
        "success": True,
        "credited_egp": amount_egp,
        "new_balance_egp": piastres_to_egp(new_bal_piastres),
        "transaction_id": tx.id,
        "message": f"Successfully credited {format_egp(amount_piastres)} to {user.full_name}.",
    }


async def admin_deduct_user_balance(
    db: AsyncSession,
    admin_id: uuid.UUID,
    user_id: uuid.UUID,
    amount_egp: float,
    reason: str,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> dict:
    """Directly debit a student account using double-entry balanced ledger transaction."""
    user = await db.scalar(select(User).where(User.id == user_id))
    if not user:
        raise NotFound(f"User {user_id}")

    amount_piastres = egp_to_piastres(amount_egp)
    user_wallet = await get_or_create_wallet(db, user_id)
    current_bal_piastres = await calculate_wallet_balance(db, user_wallet.id)

    if amount_piastres > current_bal_piastres:
        raise ProblemError(
            status_code=400,
            code="insufficient_balance",
            detail=f"Cannot deduct {format_egp(amount_piastres)}. Current balance is only {format_egp(current_bal_piastres)}.",
        )

    now = _utc_now()
    clearing_account = await get_or_create_system_clearing_account(db)

    tx = LedgerTransaction(
        idempotency_key=f"admin-topdown-{user_id}-{now.timestamp()}-{uuid.uuid4().hex[:6]}",
        reference=f"TOPDOWN-{uuid.uuid4().hex[:8].upper()}",
        description=f"Admin Top-Down: {reason}",
        status="POSTED",
        created_at=now,
        posted_at=now,
    )
    db.add(tx)
    await db.flush()

    debit_entry = LedgerEntry(
        transaction_id=tx.id,
        account_id=user_wallet.id,
        direction="DEBIT",
        amount_piastres=amount_piastres,
        created_at=now,
    )
    credit_entry = LedgerEntry(
        transaction_id=tx.id,
        account_id=clearing_account.id,
        direction="CREDIT",
        amount_piastres=amount_piastres,
        created_at=now,
    )
    db.add(debit_entry)
    db.add(credit_entry)
    await db.commit()

    new_bal_piastres = await calculate_wallet_balance(db, user_wallet.id)

    await record_audit_log(
        db,
        action="ADMIN_WALLET_DEDUCT",
        resource_type="WalletAccount",
        resource_id=str(user_wallet.id),
        actor_id=admin_id,
        actor_role="ADMIN",
        ip_address=ip_address,
        user_agent=user_agent,
        details={
            "target_user_id": str(user_id),
            "amount_piastres": amount_piastres,
            "amount_egp": amount_egp,
            "reason": reason,
            "transaction_id": str(tx.id),
        },
    )

    return {
        "success": True,
        "debited_egp": amount_egp,
        "new_balance_egp": piastres_to_egp(new_bal_piastres),
        "transaction_id": tx.id,
        "message": f"Successfully deducted {format_egp(amount_piastres)} from {user.full_name}.",
    }


async def delete_user(
    db: AsyncSession,
    *,
    admin_id: uuid.UUID,
    user_id: uuid.UUID,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> dict:
    """Permanently delete a student/user account with cascading cleanup."""
    if user_id == admin_id:
        raise ProblemError(
            status_code=400,
            code="cannot_delete_self",
            detail="Administrators cannot delete their own account.",
        )

    user = await db.scalar(select(User).where(User.id == user_id))
    if not user:
        raise NotFound(f"User {user_id}")

    # Check if user is a Super Admin
    user_roles = (await db.scalars(select(UserRole).where(UserRole.user_id == user_id))).all()
    roles = [r.role_id for r in user_roles]
    if "SUPER_ADMIN" in roles:
        all_super_admins = (
            await db.scalars(select(UserRole).where(UserRole.role_id == "SUPER_ADMIN"))
        ).all()
        if len(all_super_admins) <= 1:
            raise ProblemError(
                status_code=400,
                code="last_super_admin",
                detail="Cannot delete the platform's last Super Admin.",
            )

    # 1. Reassign voucher batches created by this user to avoid FK RESTRICT error
    # (voucher_batches.created_by has NO ACTION/RESTRICT ondelete)
    from app.modules.vouchers.models import VoucherBatch
    await db.execute(
        update(VoucherBatch).where(VoucherBatch.created_by == user_id).values(created_by=admin_id)
    )

    # 2. Detach wallet account to avoid FK RESTRICT on ledger_entries
    await db.execute(
        update(WalletAccount)
        .where(WalletAccount.user_id == user_id)
        .values(user_id=None, is_active=False)
    )

    # 3. Record audit log BEFORE deleting user entity
    await record_audit_log(
        db,
        action="ADMIN_DELETE_USER",
        resource_type="User",
        resource_id=str(user_id),
        actor_id=admin_id,
        actor_role="ADMIN",
        ip_address=ip_address,
        user_agent=user_agent,
        details={
            "deleted_user_email": user.email,
            "deleted_user_name": user.full_name,
            "deleted_user_roles": roles,
        },
    )

    # 4. Delete the User entity (cascades to devices, sessions, user_roles, quiz_attempts, entitlements)
    await db.delete(user)
    await db.commit()

    return {
        "success": True,
        "message": f"User {user.full_name} ({user.email}) permanently removed.",
        "user_id": str(user_id),
    }


async def list_registered_devices(
    db: AsyncSession,
    limit: int = 100,
) -> list[dict]:
    """List bound hardware devices with user details."""
    query = (
        select(Device, User)
        .join(User, Device.user_id == User.id)
        .order_by(Device.registered_at.desc())
        .limit(limit)
    )
    rows = (await db.execute(query)).all()
    results = []
    for dev, user in rows:
        results.append(
            {
                "id": dev.id,
                "user_id": user.id,
                "user_name": user.full_name,
                "user_email": user.email,
                "device_fingerprint": dev.device_fingerprint,
                "model": dev.model,
                "platform": dev.platform,
                "status": dev.status,
                "registered_at": dev.registered_at,
            }
        )
    return results


async def revoke_device_binding(
    db: AsyncSession,
    admin_id: uuid.UUID,
    device_id: uuid.UUID,
) -> bool:
    """Admin unbinds/revokes a hardware device registration."""
    dev = await db.scalar(select(Device).where(Device.id == device_id))
    if not dev:
        raise NotFound(f"Device {device_id}")

    dev.status = "REVOKED"
    dev.revoked_at = _utc_now()
    await db.commit()

    await record_audit_log(
        db,
        action="ADMIN_DEVICE_REVOKED",
        resource_type="Device",
        resource_id=str(dev.id),
        actor_id=admin_id,
        actor_role="ADMIN",
        details={"user_id": str(dev.user_id), "device_id": str(dev.id)},
    )
    return True


async def emergency_lockdown(
    db: AsyncSession,
    admin_id: uuid.UUID,
    identifier: str,
    reason: str,
) -> dict:
    """Emergency kill-switch: locks user account, revokes all devices & sessions."""
    ident = identifier.strip()
    user = None

    # Try UUID
    try:
        uid = uuid.UUID(ident)
        user = await db.scalar(select(User).where(User.id == uid))
    except ValueError:
        pass

    # Try phone or email
    if not user:
        user = await db.scalar(
            select(User).where(or_(User.phone == ident, User.email.ilike(ident)))
        )

    if not user:
        raise NotFound(f"Student with identifier {identifier}")

    now = _utc_now()
    user.is_active = False

    # Revoke all active devices
    devices = (
        await db.scalars(select(Device).where(Device.user_id == user.id, Device.status == "ACTIVE"))
    ).all()
    for d in devices:
        d.status = "REVOKED"
        d.revoked_at = now

    # Revoke all active sessions
    sessions = (
        await db.scalars(
            select(UserSession).where(
                UserSession.user_id == user.id, UserSession.is_revoked.is_(False)
            )
        )
    ).all()
    for s in sessions:
        s.is_revoked = True
        s.revoked_at = now

    await db.commit()

    await record_audit_log(
        db,
        action="EMERGENCY_LOCKDOWN",
        resource_type="User",
        resource_id=str(user.id),
        actor_id=admin_id,
        actor_role="ADMIN",
        details={
            "user_id": str(user.id),
            "reason": reason,
            "devices_revoked": len(devices),
            "sessions_revoked": len(sessions),
        },
    )

    msg = (
        f"EMERGENCY KILL-SWITCH EXECUTED: Account {user.email} frozen. "
        f"{len(devices)} device(s) and {len(sessions)} session(s) invalidated."
    )
    return {
        "success": True,
        "user_id": user.id,
        "user_name": user.full_name,
        "devices_revoked": len(devices),
        "sessions_revoked": len(sessions),
        "message": msg,
    }


# ==============================================================================
# Creator & Curriculum Authoring Operations
# ==============================================================================


async def create_published_course(
    db: AsyncSession,
    *,
    admin_id: uuid.UUID,
    title: str,
    description: str,
    price_egp: float,
    category: str = "Medical",
    product_type: str = "course",
    medical_year: int = 1,
    folder_id: uuid.UUID | None = None,
    preview_data: str | None = None,
    discount_percent: int = 0,
    min_discount_quantity: int = 1,
) -> dict:
    """Publish a new course with price, version, and optional volume discount."""
    price_piastres = egp_to_piastres(price_egp)
    product = Product(
        title=title,
        description=description,
        price_piastres=price_piastres,
        category=category,
        product_type=product_type,
        medical_year=medical_year,
        folder_id=folder_id,
        preview_data=preview_data,
        is_active=True,
    )
    db.add(product)
    await db.flush()

    version = ProductVersion(
        product_id=product.id,
        version_number=1,
        changelog="Initial creator release",
    )
    db.add(version)

    if discount_percent > 0:
        rule = PriceRule(
            product_id=product.id,
            min_quantity=min_discount_quantity,
            discount_percent=discount_percent,
        )
        db.add(rule)

    await db.commit()
    await db.refresh(product)

    # Auto-dispatch notification for students of this medical year
    try:
        from app.modules.notifications.service import create_notification

        notif_type = "NEW_PDF" if product_type in ("memo", "book", "pdf") else "NEW_COURSE"
        await create_notification(
            db=db,
            title=f"New Content Added: {title}",
            message=f"A new {product_type.upper()} has been added to Year {medical_year} curriculum.",
            notification_type=notif_type,
            target_medical_year=medical_year,
            resource_type="product",
            resource_id=str(product.id),
            created_by=admin_id,
        )
    except Exception:
        pass

    await record_audit_log(
        db,
        action="CREATE_COURSE",
        resource_type="Product",
        resource_id=str(product.id),
        actor_id=admin_id,
        actor_role="ADMIN",
        details={
            "title": title,
            "price_egp": price_egp,
            "category": category,
            "medical_year": medical_year,
        },
    )

    return {
        "id": product.id,
        "title": product.title,
        "description": product.description,
        "price_piastres": product.price_piastres,
        "price_egp": piastres_to_egp(product.price_piastres),
        "category": product.category,
        "product_type": product.product_type,
        "medical_year": product.medical_year,
        "folder_id": product.folder_id,
        "preview_data": getattr(product, "preview_data", None),
        "is_active": product.is_active,
        "created_at": product.created_at,
    }


def _sanitize_list_preview_data(product_id: uuid.UUID | None, preview_data: str | None) -> str | None:
    """Omit heavy inline Base64 thumbnails from list payloads to prevent massive network overhead.

    Preserves URLs and short strings. Large data URIs and raw Base64 (> 512 chars)
    are replaced with edge thumbnail URLs so the admin dashboard renders crisp thumbnails without blob overhead.
    """
    if not preview_data:
        return None
    data_str = preview_data.strip()
    if data_str.startswith("http://") or data_str.startswith("https://"):
        return data_str
    if product_id:
        edge_domain = get_settings().cloudflare_edge_domain or "https://fighters-edge-gateway.fightermedicine.workers.dev"
        edge_domain = edge_domain.rstrip("/")
        return f"{edge_domain}/v1/catalog/products/{product_id}/thumbnail?v=20260923_hd"
    if data_str.startswith("data:image/") or len(data_str) > 512:
        return None
    return data_str


async def list_admin_courses(db: AsyncSession) -> list[dict]:
    """List all courses and catalog items for admin management."""
    result = await db.scalars(select(Product).order_by(Product.created_at.desc()))
    courses = result.all()
    return [
        {
            "id": c.id,
            "title": c.title,
            "description": c.description,
            "price_piastres": c.price_piastres,
            "price_egp": piastres_to_egp(c.price_piastres),
            "category": c.category,
            "product_type": c.product_type,
            "medical_year": getattr(c, "medical_year", 1) or 1,
            "folder_id": getattr(c, "folder_id", None),
            "preview_data": _sanitize_list_preview_data(c.id, getattr(c, "preview_data", None)),
            "is_active": c.is_active,
            "created_at": c.created_at,
        }
        for c in courses
    ]


async def add_course_curriculum_item(
    db: AsyncSession,
    *,
    admin_id: uuid.UUID,
    course_id: uuid.UUID,
    title: str,
    content_type: str,
    url_or_path: str,
    duration_seconds: int = 0,
) -> dict:
    """Attach a video stream, external link, or PDF to a published course."""
    course = await db.get(Product, course_id)
    if not course:
        raise NotFound(f"Course {course_id} not found")

    item_id: uuid.UUID
    if content_type == "video":
        video = VideoAsset(
            product_id=course_id,
            vimeo_video_id=url_or_path,
            title=title,
            duration_seconds=duration_seconds,
        )
        db.add(video)
        await db.flush()
        item_id = video.id
    else:
        path_hash = hashlib.sha256(url_or_path.encode()).hexdigest()[:16]
        asset = ContentAsset(
            product_id=course_id,
            title=title,
            content_type=content_type,
            storage_path=url_or_path,
            content_hash=path_hash,
            size_bytes=1024,
            is_encrypted=False,
        )
        db.add(asset)
        await db.flush()
        item_id = asset.id

    await db.commit()

    await record_audit_log(
        db,
        action="ADD_CURRICULUM_ITEM",
        resource_type="CurriculumItem",
        resource_id=str(item_id),
        actor_id=admin_id,
        actor_role="ADMIN",
        details={
            "course_id": str(course_id),
            "title": title,
            "content_type": content_type,
        },
    )

    return {
        "id": item_id,
        "course_id": course_id,
        "title": title,
        "content_type": content_type,
        "url_or_path": url_or_path,
        "message": f"Successfully attached {content_type} to {course.title}",
    }


async def list_course_curriculum_items(
    db: AsyncSession,
    course_id: uuid.UUID,
) -> list[dict]:
    """List all video lectures, PDFs, and links attached to a course for creator control."""
    course = await db.get(Product, course_id)
    if not course:
        raise NotFound(f"Course {course_id} not found")

    videos = (
        await db.scalars(
            select(VideoAsset)
            .where(VideoAsset.product_id == course_id)
            .order_by(VideoAsset.created_at.asc())
        )
    ).all()

    assets = (
        await db.scalars(
            select(ContentAsset)
            .where(ContentAsset.product_id == course_id)
            .order_by(ContentAsset.created_at.asc())
        )
    ).all()

    items: list[dict] = []
    for v in videos:
        # Extract numeric id for vimeo poster if available
        vimeo_id = v.vimeo_video_id
        thumb_url = None
        if vimeo_id.isdigit():
            thumb_url = f"https://vimeocdn.com/video/{vimeo_id}"
        items.append({
            "id": v.id,
            "course_id": v.product_id,
            "title": v.title,
            "content_type": "video",
            "url_or_path": v.vimeo_video_id,
            "duration_seconds": v.duration_seconds,
            "thumbnail_url": thumb_url,
            "created_at": v.created_at,
        })

    for a in assets:
        items.append({
            "id": a.id,
            "course_id": a.product_id,
            "title": a.title,
            "content_type": a.content_type,
            "url_or_path": a.storage_path,
            "duration_seconds": 0,
            "thumbnail_url": None,
            "created_at": a.created_at,
        })

    return items


async def delete_course_curriculum_item(
    db: AsyncSession,
    *,
    admin_id: uuid.UUID,
    course_id: uuid.UUID,
    item_id: uuid.UUID,
) -> dict:
    """Delete a video or content asset item from a course."""
    # 1. Check VideoAsset
    video = await db.get(VideoAsset, item_id)
    if video and video.product_id == course_id:
        title = video.title
        await db.delete(video)
        await db.commit()
        await record_audit_log(
            db,
            action="DELETE_CURRICULUM_ITEM",
            resource_type="VideoAsset",
            resource_id=str(item_id),
            actor_id=admin_id,
            actor_role="ADMIN",
            details={"course_id": str(course_id), "title": title},
        )
        return {"success": True, "message": f"Deleted video lecture '{title}'"}

    # 2. Check ContentAsset
    asset = await db.get(ContentAsset, item_id)
    if asset and asset.product_id == course_id:
        title = asset.title
        await db.delete(asset)
        await db.commit()
        await record_audit_log(
            db,
            action="DELETE_CURRICULUM_ITEM",
            resource_type="ContentAsset",
            resource_id=str(item_id),
            actor_id=admin_id,
            actor_role="ADMIN",
            details={"course_id": str(course_id), "title": title},
        )
        return {"success": True, "message": f"Deleted curriculum asset '{title}'"}

    raise NotFound(f"Curriculum item {item_id} not found in course {course_id}")


async def create_mcq_quiz(
    db: AsyncSession,
    *,
    admin_id: uuid.UUID,
    title: str,
    description: str,
    category: str,
    medical_year: int = 1,
    folder_id: uuid.UUID | None = None,
    pass_percentage: int,
    time_limit_seconds: int | None,
    exam_mode: str = "PRACTICE",
    show_explanations: bool = True,
    questions_data: list,
) -> dict:
    """Author a server-authoritative clinical MCQ quiz with questions & choices."""
    if folder_id is not None:
        folder = await db.scalar(
            select(CurriculumFolder).where(CurriculumFolder.id == folder_id)
        )
        if folder:
            medical_year = folder.medical_year

    bank = QuestionBank(
        title=title,
        description=description,
        category=category,
        medical_year=medical_year,
        folder_id=folder_id,
        pass_percentage=pass_percentage,
        time_limit_seconds=time_limit_seconds,
        exam_mode=exam_mode,
        show_explanations=show_explanations,
        is_active=True,
    )
    db.add(bank)
    await db.flush()

    total_questions = 0
    for q_idx, q_in in enumerate(questions_data):
        question = Question(
            bank_id=bank.id,
            stem=q_in.stem,
            explanation=q_in.explanation,
            points=q_in.points,
            order_index=q_idx,
        )
        db.add(question)
        await db.flush()
        total_questions += 1

        target_correct_idx = None
        ca = getattr(q_in, "correct_answer", None)
        if ca is not None:
            if isinstance(ca, int) and 0 <= ca < len(q_in.options):
                target_correct_idx = ca
            elif isinstance(ca, str):
                import re
                ca_clean = ca.strip()
                letter_match = re.match(r"^(?:option\s+)?\(?([A-Fa-f])\)?\.?$", ca_clean, re.IGNORECASE)
                num_match = re.match(r"^(?:option\s+)?\(?(\d+)\)?\.?$", ca_clean, re.IGNORECASE)
                if letter_match:
                    idx = ord(letter_match.group(1).upper()) - ord("A")
                    if 0 <= idx < len(q_in.options):
                        target_correct_idx = idx
                elif num_match:
                    idx = int(num_match.group(1))
                    if 0 <= idx < len(q_in.options):
                        target_correct_idx = idx
                    elif 1 <= idx <= len(q_in.options):
                        target_correct_idx = idx - 1
                else:
                    for i, o in enumerate(q_in.options):
                        if o.text.strip().lower() == ca_clean.lower():
                            target_correct_idx = i
                            break

        # Collect options with resolved correctness
        prepared_options = []
        for opt_idx, opt_in in enumerate(q_in.options):
            is_corr = (target_correct_idx == opt_idx) if target_correct_idx is not None else opt_in.is_correct
            prepared_options.append({
                "text": opt_in.text,
                "is_correct": is_corr,
            })

        # Ensure at least one option is marked correct if none was marked
        if not any(o["is_correct"] for o in prepared_options) and prepared_options:
            prepared_options[0]["is_correct"] = True

        # Randomize options order on creation so option A is not systematically correct
        import random as _random
        _random.shuffle(prepared_options)

        for opt_idx, opt_dict in enumerate(prepared_options):
            option = QuestionOption(
                question_id=question.id,
                text=opt_dict["text"],
                is_correct=opt_dict["is_correct"],
                order_index=opt_idx,
            )
            db.add(option)

    await db.commit()
    await db.refresh(bank)

    await record_audit_log(
        db,
        action="CREATE_MCQ_QUIZ",
        resource_type="QuestionBank",
        resource_id=str(bank.id),
        actor_id=admin_id,
        actor_role="ADMIN",
        details={"title": title, "questions_count": total_questions, "medical_year": medical_year},
    )

    return {
        "id": bank.id,
        "title": bank.title,
        "description": bank.description,
        "category": bank.category,
        "medical_year": bank.medical_year,
        "folder_id": bank.folder_id,
        "pass_percentage": bank.pass_percentage,
        "questions_count": total_questions,
        "attempts_count": 0,
        "created_at": bank.created_at,
    }


async def list_admin_quizzes(db: AsyncSession) -> list[dict]:
    """List all MCQ question banks with counts for admin dashboard."""
    q_subq = (
        select(func.count(Question.id))
        .where(Question.bank_id == QuestionBank.id)
        .correlate(QuestionBank)
        .scalar_subquery()
    )
    att_subq = (
        select(func.count(QuizAttempt.id))
        .where(QuizAttempt.bank_id == QuestionBank.id)
        .correlate(QuestionBank)
        .scalar_subquery()
    )
    stmt = (
        select(
            QuestionBank,
            q_subq.label("questions_count"),
            att_subq.label("attempts_count"),
        )
        .order_by(QuestionBank.created_at.desc())
    )
    results = (await db.execute(stmt)).all()
    out = []
    for b, q_count, att_count in results:
        out.append(
            {
                "id": b.id,
                "title": b.title,
                "description": b.description,
                "category": b.category,
                "medical_year": getattr(b, "medical_year", 1) or 1,
                "folder_id": getattr(b, "folder_id", None),
                "pass_percentage": b.pass_percentage,
                "time_limit_seconds": b.time_limit_seconds,
                "is_active": b.is_active,
                "exam_mode": getattr(b, "exam_mode", "PRACTICE") or "PRACTICE",
                "show_explanations": getattr(b, "show_explanations", True),
                "questions_count": q_count or 0,
                "attempts_count": att_count or 0,
                "created_at": b.created_at,
            }
        )
    return out


async def create_flashcard_deck(
    db: AsyncSession,
    *,
    admin_id: uuid.UUID,
    title: str,
    description: str,
    category: str,
    medical_year: int = 1,
    folder_id: uuid.UUID | None = None,
    cards_data: list,
) -> dict:
    """Author a Spaced Repetition flashcard deck with individual cards."""
    if folder_id is not None:
        folder = await db.scalar(
            select(CurriculumFolder).where(CurriculumFolder.id == folder_id)
        )
        if folder:
            medical_year = folder.medical_year

    deck = Deck(
        title=title,
        description=description,
        category=category,
        medical_year=medical_year,
        folder_id=folder_id,
        is_public=True,
        user_id=admin_id,
    )
    db.add(deck)
    await db.flush()

    total_cards = 0
    for card_in in cards_data:
        card = Card(
            deck_id=deck.id,
            front=card_in.front,
            back=card_in.back,
            hint=card_in.hint,
            tags=card_in.tags,
        )
        db.add(card)
        total_cards += 1

    await db.commit()
    await db.refresh(deck)

    await record_audit_log(
        db,
        action="CREATE_FLASHCARD_DECK",
        resource_type="Deck",
        resource_id=str(deck.id),
        actor_id=admin_id,
        actor_role="ADMIN",
        details={"title": title, "cards_count": total_cards, "medical_year": medical_year},
    )

    return {
        "id": deck.id,
        "title": deck.title,
        "description": deck.description,
        "category": deck.category,
        "medical_year": deck.medical_year,
        "folder_id": deck.folder_id,
        "cards_count": total_cards,
        "created_at": deck.created_at,
    }


async def list_admin_decks(db: AsyncSession) -> list[dict]:
    """List all flashcard decks with card counts for creator dashboard."""
    c_subq = (
        select(func.count(Card.id))
        .where(Card.deck_id == Deck.id)
        .correlate(Deck)
        .scalar_subquery()
    )
    stmt = (
        select(
            Deck,
            c_subq.label("cards_count"),
        )
        .order_by(Deck.created_at.desc())
    )
    results = (await db.execute(stmt)).all()
    out = []
    for d, c_count in results:
        out.append(
            {
                "id": d.id,
                "title": d.title,
                "description": d.description,
                "category": d.category,
                "medical_year": getattr(d, "medical_year", 1) or 1,
                "folder_id": getattr(d, "folder_id", None),
                "cards_count": c_count or 0,
                "created_at": d.created_at,
            }
        )
    return out


# ==============================================================================
# PDF Upload (Real Server-Side Storage)
# ==============================================================================

import os  # noqa: E402
import tempfile  # noqa: E402
import urllib.error  # noqa: E402
import urllib.request  # noqa: E402

_UPLOAD_DIR = os.path.join(
    os.path.dirname(__file__),  # .../app/modules/admin/
    "..", "..", "..",            # -> project root / backend
    "storage", "uploads", "pdfs",
)


def _ensure_upload_dir() -> str:
    """Create the server PDF storage directory if it doesn't exist, return its absolute path.

    Gracefully falls back to the system temp directory in serverless / read-only environments.
    """
    target = os.path.normpath(_UPLOAD_DIR)
    try:
        os.makedirs(target, exist_ok=True)
        test_file = os.path.join(target, f".perm_test_{os.getpid()}")
        with open(test_file, "w") as f:
            f.write("ok")
        os.remove(test_file)
        return target
    except (OSError, PermissionError):
        tmp_dir = os.path.normpath(os.path.join(tempfile.gettempdir(), "medfighter", "uploads", "pdfs"))
        os.makedirs(tmp_dir, exist_ok=True)
        return tmp_dir


def _upload_to_supabase_storage(filename: str, file_bytes: bytes) -> str | None:
    """Upload PDF file to Supabase Storage bucket fighters-pdfs and return public URL."""
    supabase_url = os.environ.get("FIGHTERS_SUPABASE_URL", "https://phkwfuthmnyuvwfxnbrx.supabase.co").rstrip("/")
    supabase_key = os.environ.get("FIGHTERS_SUPABASE_KEY", "sb_publishable_D9-Rh_ti_XR9coLsUzpA6Q_iPFRyKjQ")
    url = f"{supabase_url}/storage/v1/object/fighters-pdfs/{filename}"
    req = urllib.request.Request(
        url,
        data=file_bytes,
        headers={
            "apikey": supabase_key,
            "Authorization": f"Bearer {supabase_key}",
            "Content-Type": "application/pdf",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            if resp.status in (200, 201):
                return f"{supabase_url}/storage/v1/object/public/fighters-pdfs/{filename}"
    except urllib.error.HTTPError as e:
        if e.code == 400:
            return f"{supabase_url}/storage/v1/object/public/fighters-pdfs/{filename}"
        logger.warning("Supabase storage upload HTTP error %s: %s", e.code, e.reason)
    except Exception as exc:
        logger.warning("Supabase storage upload error: %s", exc)
    return None


async def upload_pdf_document(
    db: AsyncSession,
    *,
    admin_id: uuid.UUID,
    file_bytes: bytes,
    original_filename: str,
    title: str,
    description: str,
    price_egp: float = 0.0,
    category: str = "Medical",
    medical_year: int = 1,
    folder_id: uuid.UUID | None = None,
    preview_data: str | None = None,
) -> dict:
    """Save uploaded PDF to server storage and create a catalog Product + ContentAsset record."""
    import hashlib as _hashlib
    from app.modules.content.models import ContentAssetFile

    # 1. Best-effort persist file to local disk / /tmp
    upload_dir = _ensure_upload_dir()
    file_hash = _hashlib.sha256(file_bytes).hexdigest()
    # Sanitize filename — keep only alphanumeric, dash, underscore, dot
    safe_name = "".join(c if c.isalnum() or c in (".", "-", "_") else "_" for c in original_filename)
    stored_filename = f"{file_hash[:12]}_{safe_name}"
    stored_path = os.path.join(upload_dir, stored_filename)

    try:
        with open(stored_path, "wb") as fh:
            fh.write(file_bytes)
    except Exception as exc:
        logger.warning("Failed writing PDF to local path %s: %s", stored_path, exc)

    # 2. Persist to Supabase Storage (globally accessible CDN)
    public_url = _upload_to_supabase_storage(stored_filename, file_bytes)
    final_storage_path = public_url if public_url else stored_path

    # 3. Create Product catalog entry (product_type = 'memo')
    if folder_id is not None:
        folder = await db.scalar(
            select(CurriculumFolder).where(CurriculumFolder.id == folder_id)
        )
        if folder:
            medical_year = folder.medical_year

    price_piastres = egp_to_piastres(price_egp)
    product = Product(
        title=title,
        description=description,
        price_piastres=price_piastres,
        category=category,
        product_type="memo",
        medical_year=medical_year,
        folder_id=folder_id,
        preview_data=preview_data,
        is_active=True,
    )
    db.add(product)
    await db.flush()

    # 4. Attach ContentAsset so students can access it
    asset = ContentAsset(
        product_id=product.id,
        title=title,
        content_type="pdf",
        storage_path=final_storage_path,
        content_hash=file_hash,
        size_bytes=len(file_bytes),
        is_encrypted=False,
    )
    db.add(asset)
    await db.flush()

    # 5. Persist bytes directly in PostgreSQL content_asset_files table
    # This guarantees 100% availability across all Vercel serverless containers
    asset_file = ContentAssetFile(
        asset_id=asset.id,
        file_bytes=file_bytes,
    )
    db.add(asset_file)

    # 6. Version record
    version = ProductVersion(
        product_id=product.id,
        version_number=1,
        changelog="Initial PDF upload",
    )
    db.add(version)

    await db.commit()
    await db.refresh(product)
    await db.refresh(asset)

    # 7. Notify students
    try:
        from app.modules.notifications.service import create_notification

        await create_notification(
            db=db,
            title=f"New PDF Added: {title}",
            message=f"A new PDF document has been added to Year {medical_year} curriculum.",
            notification_type="NEW_PDF",
            target_medical_year=medical_year,
            resource_type="product",
            resource_id=str(product.id),
            created_by=admin_id,
        )
    except Exception:
        pass

    await record_audit_log(
        db,
        action="UPLOAD_PDF",
        resource_type="Product",
        resource_id=str(product.id),
        actor_id=admin_id,
        actor_role="ADMIN",
        details={
            "title": title,
            "filename": original_filename,
            "stored_path": final_storage_path,
            "size_bytes": len(file_bytes),
            "medical_year": medical_year,
        },
    )

    return {
        "id": product.id,
        "title": product.title,
        "description": product.description,
        "price_piastres": product.price_piastres,
        "price_egp": piastres_to_egp(product.price_piastres),
        "category": product.category,
        "product_type": product.product_type,
        "medical_year": product.medical_year,
        "folder_id": product.folder_id,
        "preview_data": getattr(product, "preview_data", None),
        "is_active": product.is_active,
        "created_at": product.created_at,
        "asset_id": asset.id,
        "stored_filename": stored_filename,
        "size_bytes": len(file_bytes),
    }



# ==============================================================================
# Delete Operations
# ==============================================================================


async def delete_course(
    db: AsyncSession,
    *,
    admin_id: uuid.UUID,
    course_id: uuid.UUID,
) -> dict:
    """Permanently delete a course, purging all related assets, licenses, and catalog records."""
    product = await db.get(Product, course_id)
    if not product:
        raise NotFound(f"Course {course_id}")
    title = product.title

    bind = db.bind or getattr(db.sync_session, "bind", None)
    is_pg = bind is not None and "postgresql" in bind.dialect.name

    if is_pg:
        # Atomic Data-Modifying CTE: Consolidates all cascade unlinking and deletions into 1 single round-trip
        cte_stmt = text("""
            WITH del_licenses AS (
                DELETE FROM device_licenses
                WHERE entitlement_id IN (SELECT id FROM entitlements WHERE product_id = :cid)
                RETURNING id
            ),
            del_entitlements AS (
                DELETE FROM entitlements WHERE product_id = :cid
                RETURNING id
            ),
            del_purchase_units AS (
                DELETE FROM purchase_units WHERE product_id = :cid
                RETURNING id
            ),
            del_orders AS (
                DELETE FROM orders WHERE product_id = :cid
                RETURNING id
            ),
            upd_payments AS (
                UPDATE payments SET product_id = NULL WHERE product_id = :cid
                RETURNING id
            ),
            upd_vouchers AS (
                UPDATE vouchers SET product_id = NULL WHERE product_id = :cid
                RETURNING id
            ),
            del_bundle_items AS (
                DELETE FROM bundle_items
                WHERE bundle_id IN (SELECT id FROM bundles WHERE product_id = :cid)
                   OR item_product_id = :cid
                RETURNING id
            ),
            del_bundles AS (
                DELETE FROM bundles WHERE product_id = :cid
                RETURNING id
            ),
            del_price_rules AS (
                DELETE FROM price_rules WHERE product_id = :cid
                RETURNING id
            ),
            del_versions AS (
                DELETE FROM product_versions WHERE product_id = :cid
                RETURNING id
            ),
            del_sessions AS (
                DELETE FROM video_sessions
                WHERE video_asset_id IN (SELECT id FROM video_assets WHERE product_id = :cid)
                RETURNING id
            ),
            del_videos AS (
                DELETE FROM video_assets WHERE product_id = :cid
                RETURNING id
            ),
            del_content AS (
                DELETE FROM content_assets WHERE product_id = :cid
                RETURNING id
            )
            DELETE FROM products WHERE id = :cid;
        """)
        await db.execute(cte_stmt, {"cid": str(course_id)})
    else:
        # Sequential execution fallback for non-PostgreSQL (e.g. SQLite test client)
        await db.execute(update(Payment).where(Payment.product_id == course_id).values(product_id=None))
        subq_ent = select(Entitlement.id).where(Entitlement.product_id == course_id)
        await db.execute(delete(DeviceLicense).where(DeviceLicense.entitlement_id.in_(subq_ent)))
        await db.execute(delete(Entitlement).where(Entitlement.product_id == course_id))
        await db.execute(delete(PurchaseUnit).where(PurchaseUnit.product_id == course_id))
        await db.execute(delete(Order).where(Order.product_id == course_id))
        await db.execute(update(Voucher).where(Voucher.product_id == course_id).values(product_id=None))
        subq_bundle = select(Bundle.id).where(Bundle.product_id == course_id)
        await db.execute(
            delete(BundleItem).where(
                or_(
                    BundleItem.bundle_id.in_(subq_bundle),
                    BundleItem.item_product_id == course_id,
                )
            )
        )
        await db.execute(delete(Bundle).where(Bundle.product_id == course_id))
        await db.execute(delete(PriceRule).where(PriceRule.product_id == course_id))
        await db.execute(delete(ProductVersion).where(ProductVersion.product_id == course_id))
        subq_video = select(VideoAsset.id).where(VideoAsset.product_id == course_id)
        await db.execute(delete(VideoSession).where(VideoSession.video_asset_id.in_(subq_video)))
        await db.execute(delete(VideoAsset).where(VideoAsset.product_id == course_id))
        await db.execute(delete(ContentAsset).where(ContentAsset.product_id == course_id))
        await db.execute(delete(Product).where(Product.id == course_id))

    # Record audit log BEFORE commit so entire purge is atomic
    await record_audit_log(
        db,
        action="DELETE_COURSE",
        resource_type="Product",
        resource_id=str(course_id),
        actor_id=admin_id,
        actor_role="ADMIN",
        details={"title": title},
    )
    await db.commit()
    return {"success": True, "id": course_id, "message": f'Course "{title}" permanently deleted.'}


async def delete_quiz(
    db: AsyncSession,
    *,
    admin_id: uuid.UUID,
    quiz_id: uuid.UUID,
) -> dict:
    """Hard-delete a quiz bank and all its questions / options."""
    bank = await db.get(QuestionBank, quiz_id)
    if not bank:
        raise NotFound(f"QuestionBank {quiz_id}")
    title = bank.title

    await record_audit_log(
        db,
        action="DELETE_QUIZ",
        resource_type="QuestionBank",
        resource_id=str(quiz_id),
        actor_id=admin_id,
        actor_role="ADMIN",
        details={"title": title},
    )
    # Cascade deletes handled by FK ondelete in models
    await db.delete(bank)
    await db.commit()
    return {"success": True, "id": quiz_id, "message": f'Quiz bank "{title}" deleted.'}


async def delete_deck(
    db: AsyncSession,
    *,
    admin_id: uuid.UUID,
    deck_id: uuid.UUID,
) -> dict:
    """Hard-delete a flashcard deck and all its cards."""
    deck = await db.get(Deck, deck_id)
    if not deck:
        raise NotFound(f"Deck {deck_id}")
    title = deck.title

    await record_audit_log(
        db,
        action="DELETE_DECK",
        resource_type="Deck",
        resource_id=str(deck_id),
        actor_id=admin_id,
        actor_role="ADMIN",
        details={"title": title},
    )
    await db.delete(deck)
    await db.commit()
    return {"success": True, "id": deck_id, "message": f'Flashcard deck "{title}" deleted.'}


# ==============================================================================
# Admin Team & Role Management (§5, §6)
# ==============================================================================


async def list_admin_team(db: AsyncSession) -> list[dict]:
    """List all registered administrators (Super Admins and Standard Admins)."""
    # Find all user roles with role_id in ('ADMIN', 'SUPER_ADMIN')
    query = (
        select(UserRole)
        .where(UserRole.role_id.in_(["ADMIN", "SUPER_ADMIN"]))
        .order_by(UserRole.assigned_at.desc())
    )
    user_roles = (await db.scalars(query)).all()

    # Group by user_id
    admin_user_ids = {ur.user_id for ur in user_roles}
    if not admin_user_ids:
        return []

    # Fetch full users with their complete roles
    users_query = select(User).where(User.id.in_(admin_user_ids))
    users = (await db.scalars(users_query)).all()

    # Map roles per user
    all_roles_query = select(UserRole).where(UserRole.user_id.in_(admin_user_ids))
    all_ur = (await db.scalars(all_roles_query)).all()
    roles_by_user: dict[uuid.UUID, list[str]] = {}
    assigned_at_by_user: dict[uuid.UUID, datetime] = {}
    for ur in all_ur:
        roles_by_user.setdefault(ur.user_id, []).append(ur.role_id)
        # Record latest assignment safely comparing naive/aware datetimes
        current_assigned = ur.assigned_at or _utc_now()
        if ur.user_id not in assigned_at_by_user:
            assigned_at_by_user[ur.user_id] = current_assigned
        else:
            prev = assigned_at_by_user[ur.user_id]
            curr_aware = current_assigned if current_assigned.tzinfo is not None else current_assigned.replace(tzinfo=UTC)
            prev_aware = prev if prev.tzinfo is not None else prev.replace(tzinfo=UTC)
            if curr_aware > prev_aware:
                assigned_at_by_user[ur.user_id] = current_assigned

    result = []
    for u in users:
        u_roles = roles_by_user.get(u.id, [])
        is_super = "SUPER_ADMIN" in u_roles
        primary_role = "SUPER_ADMIN" if is_super else "ADMIN"
        result.append(
            {
                "id": u.id,
                "email": u.email,
                "full_name": u.full_name,
                "phone": u.phone,
                "role": primary_role,
                "can_add_admins": is_super,
                "roles": u_roles,
                "assigned_at": assigned_at_by_user.get(u.id, u.created_at),
                "is_active": u.is_active,
            }
        )

    # Sort: Super Admins first, then by name
    result.sort(key=lambda x: (0 if x["role"] == "SUPER_ADMIN" else 1, (x.get("full_name") or "").lower()))
    return result


async def promote_user_to_admin(
    db: AsyncSession,
    *,
    actor_admin_id: uuid.UUID,
    identifier: str,
    role: str | None = None,
    target_role: str | None = None,
) -> dict:
    """Promote an existing student to Admin or Super Admin, or change an existing admin's tier."""
    chosen_role = (target_role or role or "ADMIN").strip().upper()
    if chosen_role not in ["ADMIN", "SUPER_ADMIN", "CREATOR"]:
        raise ProblemError(
            status_code=400,
            code="invalid_role",
            detail=f"Role must be 'ADMIN', 'SUPER_ADMIN', or 'CREATOR', got '{chosen_role}'.",
        )

    clean_id = identifier.strip()

    # Search by UUID, exact email, or exact phone
    user: User | None = None
    try:
        user_uuid = uuid.UUID(clean_id)
        user = await db.get(User, user_uuid)
    except ValueError:
        pass

    if not user:
        user = await db.scalar(
            select(User).where(
                or_(
                    func.lower(User.email) == clean_id.lower(),
                    User.phone == clean_id,
                    func.lower(User.full_name) == clean_id.lower(),
                    User.full_name.ilike(f"%{clean_id}%"),
                )
            )
        )

    if not user:
        raise ProblemError(
            status_code=404,
            code="user_not_found",
            detail=f"No student found matching '{clean_id}'. Please check the email, phone, or name.",
        )

    # Ensure roles exist in roles table
    for r_id, desc in [
        ("USER", "Learner / Student"),
        ("CREATOR", "Content Creator / Instructor"),
        ("ADMIN", "Platform Admin / Instructor"),
        ("SUPER_ADMIN", "Super Administrator / Chief Creator"),
    ]:
        if not await db.scalar(select(Role).where(Role.id == r_id)):
            db.add(Role(id=r_id, description=desc))
    await db.flush()

    # Fetch existing user roles
    existing_roles = (
        await db.scalars(select(UserRole).where(UserRole.user_id == user.id))
    ).all()
    existing_role_ids = {ur.role_id for ur in existing_roles}

    now = _utc_now()

    if chosen_role == "SUPER_ADMIN":
        # Super Admin gets both ADMIN and SUPER_ADMIN
        for r in ["ADMIN", "SUPER_ADMIN"]:
            if r not in existing_role_ids:
                db.add(UserRole(user_id=user.id, role_id=r, assigned_at=now))
    elif chosen_role == "ADMIN":
        # Standard Admin gets ADMIN, but NOT SUPER_ADMIN
        if "ADMIN" not in existing_role_ids:
            db.add(UserRole(user_id=user.id, role_id="ADMIN", assigned_at=now))
        # If user previously had SUPER_ADMIN, demote tier to standard ADMIN
        for ur in existing_roles:
            if ur.role_id == "SUPER_ADMIN":
                await db.delete(ur)
    elif chosen_role == "CREATOR":
        if "CREATOR" not in existing_role_ids:
            db.add(UserRole(user_id=user.id, role_id="CREATOR", assigned_at=now))

    # Record authoritative audit log
    await record_audit_log(
        db,
        action="PROMOTE_ADMIN",
        resource_type="User",
        resource_id=str(user.id),
        actor_id=actor_admin_id,
        actor_role="SUPER_ADMIN",
        details={
            "target_user_id": str(user.id),
            "target_email": user.email,
            "target_full_name": user.full_name,
            "assigned_role": chosen_role,
        },
    )
    await db.commit()

    # Refresh and return
    updated_roles = (
        await db.scalars(select(UserRole.role_id).where(UserRole.user_id == user.id))
    ).all()

    return {
        "id": str(user.id),
        "email": user.email,
        "full_name": user.full_name,
        "phone": user.phone,
        "role": chosen_role,
        "can_add_admins": chosen_role == "SUPER_ADMIN",
        "roles": list(updated_roles),
        "assigned_at": now.isoformat(),
        "is_active": user.is_active,
        "message": f"Successfully promoted {user.full_name} ({user.email}) to {chosen_role}.",
    }


async def demote_admin_user(
    db: AsyncSession,
    *,
    actor_admin_id: uuid.UUID,
    target_user_id: uuid.UUID,
) -> dict:
    """Revoke admin privileges from a user and restore them to standard student/user."""
    if actor_admin_id == target_user_id:
        raise ProblemError(
            status_code=400,
            code="cannot_demote_self",
            detail="You cannot revoke your own administrator access.",
        )

    user = await db.get(User, target_user_id)
    if not user:
        raise ProblemError(status_code=404, code="not_found", detail="User not found.")

    # Check if target is Super Admin and if this is the last Super Admin
    super_admin_count = await db.scalar(
        select(func.count(UserRole.user_id)).where(UserRole.role_id == "SUPER_ADMIN")
    ) or 0

    target_roles = (
        await db.scalars(select(UserRole).where(UserRole.user_id == target_user_id))
    ).all()
    target_role_ids = {ur.role_id for ur in target_roles}

    if "SUPER_ADMIN" in target_role_ids and super_admin_count <= 1:
        raise ProblemError(
            status_code=400,
            code="last_super_admin",
            detail="Cannot demote the last remaining Super Administrator on the platform.",
        )

    # Remove ADMIN and SUPER_ADMIN roles
    for ur in target_roles:
        if ur.role_id in ["ADMIN", "SUPER_ADMIN"]:
            await db.delete(ur)

    # Ensure USER role remains
    if "USER" not in target_role_ids:
        db.add(UserRole(user_id=target_user_id, role_id="USER", assigned_at=_utc_now()))

    await db.commit()

    # Audit log
    await record_audit_log(
        db,
        action="DEMOTE_ADMIN",
        resource_type="User",
        resource_id=str(target_user_id),
        actor_id=actor_admin_id,
        actor_role="SUPER_ADMIN",
        details={
            "target_user_id": str(target_user_id),
            "target_email": user.email,
        },
    )

    return {
        "success": True,
        "message": f"Successfully revoked administrator privileges from {user.full_name} ({user.email}).",
    }


# ==============================================================================
# Course Update & Platform Contact Info Service Functions
# ==============================================================================


async def update_published_course(
    db: AsyncSession,
    *,
    admin_id: uuid.UUID,
    course_id: uuid.UUID,
    payload: AdminCourseUpdateRequest,
) -> dict:
    course = await db.get(Product, course_id)
    if not course:
        raise ProblemError(status_code=404, code="not_found", detail="Course not found.")

    if payload.title is not None:
        course.title = payload.title.strip()
    if payload.description is not None:
        course.description = payload.description.strip()
    if payload.price_egp is not None:
        course.price_piastres = int(round(payload.price_egp * 100))
    if payload.category is not None:
        course.category = payload.category.strip()
    if payload.medical_year is not None:
        course.medical_year = payload.medical_year
    if "folder_id" in payload.model_fields_set:
        course.folder_id = payload.folder_id
        if payload.folder_id is not None:
            folder = await db.scalar(
                select(CurriculumFolder).where(CurriculumFolder.id == payload.folder_id)
            )
            if folder:
                course.medical_year = folder.medical_year
    if "preview_data" in payload.model_fields_set:
        course.preview_data = payload.preview_data
    if payload.is_active is not None:
        course.is_active = payload.is_active

    await db.commit()
    await db.refresh(course)

    await record_audit_log(
        db,
        action="UPDATE_COURSE",
        resource_type="Product",
        resource_id=str(course_id),
        actor_id=admin_id,
        actor_role="ADMIN",
        details={
            "title": course.title,
            "price_piastres": course.price_piastres,
            "medical_year": course.medical_year,
            "is_active": course.is_active,
        },
    )

    return {
        "id": course.id,
        "title": course.title,
        "description": course.description,
        "price_piastres": course.price_piastres,
        "price_egp": course.price_piastres / 100.0,
        "category": course.category,
        "product_type": course.product_type,
        "medical_year": course.medical_year,
        "folder_id": course.folder_id,
        "preview_data": getattr(course, "preview_data", None),
        "is_active": course.is_active,
    }


async def update_mcq_quiz(
    db: AsyncSession,
    *,
    admin_id: uuid.UUID,
    quiz_id: uuid.UUID,
    payload: AdminQuizUpdateRequest,
) -> dict:
    quiz = await db.get(QuestionBank, quiz_id)
    if not quiz:
        raise ProblemError(status_code=404, code="not_found", detail="MCQ quiz not found.")

    if payload.title is not None:
        quiz.title = payload.title.strip()
    if payload.description is not None:
        quiz.description = payload.description.strip()
    if payload.category is not None:
        quiz.category = payload.category.strip()
    if payload.medical_year is not None:
        quiz.medical_year = payload.medical_year
    if "folder_id" in payload.model_fields_set:
        quiz.folder_id = payload.folder_id
    if payload.pass_percentage is not None:
        quiz.pass_percentage = payload.pass_percentage
    if payload.time_limit_seconds is not None:
        quiz.time_limit_seconds = payload.time_limit_seconds
    if payload.is_active is not None:
        quiz.is_active = payload.is_active
    if payload.exam_mode is not None:
        quiz.exam_mode = payload.exam_mode.strip()
    if payload.show_explanations is not None:
        quiz.show_explanations = payload.show_explanations

    await db.commit()
    await db.refresh(quiz)

    await record_audit_log(
        db,
        action="UPDATE_QUIZ",
        resource_type="QuestionBank",
        resource_id=str(quiz_id),
        actor_id=admin_id,
        actor_role="ADMIN",
        details={"title": quiz.title, "folder_id": str(quiz.folder_id) if quiz.folder_id else None},
    )

    return {
        "id": quiz.id,
        "title": quiz.title,
        "description": quiz.description,
        "category": quiz.category,
        "medical_year": quiz.medical_year,
        "folder_id": quiz.folder_id,
        "pass_percentage": quiz.pass_percentage,
        "time_limit_seconds": quiz.time_limit_seconds,
        "is_active": quiz.is_active,
        "exam_mode": getattr(quiz, "exam_mode", "PRACTICE") or "PRACTICE",
        "show_explanations": getattr(quiz, "show_explanations", True),
    }


async def get_quiz_admin_results(db: AsyncSession, quiz_id: uuid.UUID) -> dict:
    """Fetch complete student results ledger and telemetry metrics for a quiz."""
    quiz = await db.get(QuestionBank, quiz_id)
    if not quiz:
        raise ProblemError(status_code=404, code="not_found", detail="MCQ quiz not found.")

    from app.modules.identity.models import User

    stmt = (
        select(QuizAttempt, User)
        .join(User, User.id == QuizAttempt.user_id)
        .where(QuizAttempt.bank_id == quiz_id, QuizAttempt.completed_at.is_not(None))
        .order_by(QuizAttempt.score.desc(), QuizAttempt.completed_at.desc())
    )
    rows = (await db.execute(stmt)).all()

    attempts = []
    unique_users = set()
    total_pct = 0.0
    highest_pct = 0.0
    lowest_pct = 100.0 if rows else 0.0
    pass_count = 0
    fail_count = 0

    for att, user in rows:
        unique_users.add(user.id)
        pct = float(att.percentage or 0.0)
        total_pct += pct
        if pct > highest_pct:
            highest_pct = pct
        if pct < lowest_pct:
            lowest_pct = pct

        if att.passed:
            pass_count += 1
        else:
            fail_count += 1

        time_spent = 0
        if att.completed_at and att.started_at:
            time_spent = max(0, int((att.completed_at - att.started_at).total_seconds()))

        attempts.append(
            {
                "attempt_id": att.id,
                "user_id": user.id,
                "student_name": user.full_name or "Unknown",
                "student_email": user.email,
                "student_phone": user.phone,
                "medical_year": user.medical_year or 1,
                "score": att.score,
                "max_score": att.max_score,
                "percentage": round(pct, 1),
                "passed": att.passed,
                "started_at": att.started_at,
                "completed_at": att.completed_at,
                "time_spent_seconds": time_spent,
            }
        )

    total_attempts = len(attempts)
    avg_pct = round(total_pct / total_attempts, 1) if total_attempts > 0 else 0.0
    pass_rate = round((pass_count / total_attempts) * 100, 1) if total_attempts > 0 else 0.0

    return {
        "quiz_id": quiz.id,
        "quiz_title": quiz.title,
        "exam_mode": getattr(quiz, "exam_mode", "PRACTICE") or "PRACTICE",
        "pass_percentage": quiz.pass_percentage,
        "time_limit_seconds": quiz.time_limit_seconds,
        "summary": {
            "total_attempts": total_attempts,
            "total_students": len(unique_users),
            "average_percentage": avg_pct,
            "highest_percentage": round(highest_pct, 1),
            "lowest_percentage": round(lowest_pct, 1),
            "pass_rate_percentage": pass_rate,
            "pass_count": pass_count,
            "fail_count": fail_count,
        },
        "attempts": attempts,
    }


async def update_flashcard_deck(
    db: AsyncSession,
    *,
    admin_id: uuid.UUID,
    deck_id: uuid.UUID,
    payload: AdminDeckUpdateRequest,
) -> dict:
    deck = await db.get(Deck, deck_id)
    if not deck:
        raise ProblemError(status_code=404, code="not_found", detail="Flashcard deck not found.")

    if payload.title is not None:
        deck.title = payload.title.strip()
    if payload.description is not None:
        deck.description = payload.description.strip()
    if payload.category is not None:
        deck.category = payload.category.strip()
    if payload.medical_year is not None:
        deck.medical_year = payload.medical_year
    if "folder_id" in payload.model_fields_set:
        deck.folder_id = payload.folder_id
    if payload.is_public is not None:
        deck.is_public = payload.is_public

    await db.commit()
    await db.refresh(deck)

    await record_audit_log(
        db,
        action="UPDATE_DECK",
        resource_type="Deck",
        resource_id=str(deck_id),
        actor_id=admin_id,
        actor_role="ADMIN",
        details={"title": deck.title, "folder_id": str(deck.folder_id) if deck.folder_id else None},
    )

    return {
        "id": deck.id,
        "title": deck.title,
        "description": deck.description,
        "category": deck.category,
        "medical_year": deck.medical_year,
        "folder_id": deck.folder_id,
        "is_public": deck.is_public,
    }


async def get_contact_info(db: AsyncSession) -> dict:
    try:
        setting = await db.get(PlatformSetting, "contact_info")
        if setting and setting.value:
            val = dict(setting.value)
            val["updated_at"] = setting.updated_at
            return val
    except Exception:
        pass
    return {
        "vodafone_cash_number": "01004128527",
        "telegram_bot_username": "@MedFighter_bot",
        "telegram_bot_token": "8505734437:AAG9QSGJ87GtpW7qGngcAxZO6cWAHoE8g3w",
        "admin_telegram_1": "@Mohamed_Hamed_Samaha",
        "admin_telegram_2": "@Moh_gom3a",
        "updated_at": None,
    }


async def update_contact_info(
    db: AsyncSession,
    *,
    admin_id: uuid.UUID,
    payload: ContactInfoUpdate,
) -> dict:
    now = _utc_now()
    setting = await db.get(PlatformSetting, "contact_info")
    if not setting:
        setting = PlatformSetting(
            key="contact_info",
            value=payload.model_dump(),
            updated_at=now,
            updated_by=admin_id,
        )
        db.add(setting)
    else:
        setting.value = payload.model_dump()
        setting.updated_at = now
        setting.updated_by = admin_id
    await db.commit()

    await record_audit_log(
        db,
        action="UPDATE_CONTACT_INFO",
        resource_type="PlatformSettings",
        resource_id="contact_info",
        actor_id=admin_id,
        actor_role="ADMIN",
        details=payload.model_dump(),
    )

    return await get_contact_info(db)


async def get_security_settings(db: AsyncSession) -> dict:
    try:
        setting = await db.get(PlatformSetting, "security_settings")
        if setting and setting.value:
            val = dict(setting.value)
            val["updated_at"] = setting.updated_at
            return val
    except Exception:
        pass
    return {
        "allow_screenshots": False,
        "updated_at": None,
    }


async def update_security_settings(
    db: AsyncSession,
    *,
    admin_id: uuid.UUID,
    payload: SecuritySettingsUpdate,
) -> dict:
    now = _utc_now()
    setting = await db.get(PlatformSetting, "security_settings")
    if not setting:
        setting = PlatformSetting(
            key="security_settings",
            value=payload.model_dump(),
            updated_at=now,
            updated_by=admin_id,
        )
        db.add(setting)
    else:
        setting.value = payload.model_dump()
        setting.updated_at = now
        setting.updated_by = admin_id
    await db.commit()

    await record_audit_log(
        db,
        action="UPDATE_SECURITY_SETTINGS",
        resource_type="PlatformSettings",
        resource_id="security_settings",
        actor_id=admin_id,
        actor_role="ADMIN",
        details=payload.model_dump(),
    )

    return await get_security_settings(db)




