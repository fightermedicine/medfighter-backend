"""Voucher & Promotion domain service (§11, §35).

Manages:
- Batch scratch-card voucher minting with unique cryptographic codes.
- Atomic voucher redemption with double-entry ledger funding or course entitlement grant.
- Full audit logging and collision-resistant formatting.
"""

from __future__ import annotations

import secrets
import string
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, NotFound, ProblemError
from app.core.money import egp_to_piastres, format_egp, piastres_to_egp
from app.modules.audit.service import record_audit_log
from app.modules.catalog.models import Product
from app.modules.entitlement.models import Entitlement
from app.modules.vouchers.models import Voucher, VoucherBatch, VoucherRedemption
from app.modules.vouchers.schemas import RedeemVoucherResponse, VoucherBatchCreate, VoucherOut
from app.modules.wallet.models import LedgerEntry, LedgerTransaction
from app.modules.wallet.service import (
    calculate_wallet_balance,
    get_or_create_system_clearing_account,
    get_or_create_wallet,
)

_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # No 0, O, 1, I to prevent confusion


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _generate_code(prefix: str = "FGHT") -> str:
    """Generate a readable, scratch-card style code: FGHT-XXXX-XXXX-XXXX."""
    p1 = "".join(secrets.choice(_CHARS) for _ in range(4))
    p2 = "".join(secrets.choice(_CHARS) for _ in range(4))
    p3 = "".join(secrets.choice(_CHARS) for _ in range(4))
    return f"{prefix}-{p1}-{p2}-{p3}"


def _normalize_code(code: str) -> str:
    return code.strip().upper().replace(" ", "").replace("-", "")


async def create_voucher_batch(
    db: AsyncSession,
    payload: VoucherBatchCreate,
    admin_id: uuid.UUID,
) -> VoucherBatch:
    """Admin mints a batch of unique voucher / promo codes."""
    now = _utc_now()
    batch_suffix = "".join(secrets.choice(string.digits) for _ in range(6))
    batch_code = f"BATCH-{now.strftime('%Y%m%d')}-{batch_suffix}"

    batch = VoucherBatch(
        name=payload.name,
        batch_code=batch_code,
        created_by=admin_id,
        created_at=now,
    )
    db.add(batch)
    await db.flush()

    credit_piastres = 0
    if payload.voucher_type == "WALLET_CREDIT":
        if not payload.credit_amount_egp or payload.credit_amount_egp <= 0:
            raise ProblemError(
                status=400,
                title="Invalid Credit Amount",
                detail="credit_amount_egp must be greater than zero for WALLET_CREDIT vouchers",
            )
        credit_piastres = egp_to_piastres(payload.credit_amount_egp)

    if payload.voucher_type == "COURSE_UNLOCK":
        if not payload.product_id:
            raise ProblemError(
                status=400,
                title="Missing Product ID",
                detail="product_id is required for COURSE_UNLOCK vouchers",
            )
        # Verify product exists
        product = await db.scalar(select(Product).where(Product.id == payload.product_id))
        if not product:
            raise NotFound(f"Product {payload.product_id}")

    expires_at = None
    if payload.expires_in_days:
        expires_at = now + timedelta(days=payload.expires_in_days)

    # Mint discrete vouchers
    for _ in range(payload.count):
        # Retry loop for theoretical collision resistance
        code = None
        for _attempt in range(5):
            candidate = _generate_code()
            exists = await db.scalar(select(Voucher).where(Voucher.code == candidate))
            if not exists:
                code = candidate
                break
        if not code:
            code = f"FGHT-{uuid.uuid4().hex[:12].upper()}"

        voucher = Voucher(
            batch_id=batch.id,
            code=code,
            voucher_type=payload.voucher_type,
            credit_piastres=credit_piastres,
            product_id=payload.product_id,
            max_redemptions=payload.max_redemptions_per_code,
            redemptions_count=0,
            is_active=True,
            expires_at=expires_at,
            created_at=now,
        )
        db.add(voucher)

    await db.commit()
    await db.refresh(batch)

    await record_audit_log(
        db,
        action="VOUCHER_BATCH_CREATED",
        resource_type="VoucherBatch",
        resource_id=str(batch.id),
        actor_id=admin_id,
        actor_role="ADMIN",
        details={
            "batch_code": batch_code,
            "count": payload.count,
            "voucher_type": payload.voucher_type,
            "credit_piastres": credit_piastres,
            "product_id": str(payload.product_id) if payload.product_id else None,
        },
    )

    return batch


async def redeem_voucher(
    db: AsyncSession,
    user_id: uuid.UUID,
    raw_code: str,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> RedeemVoucherResponse:
    """Student redeems a voucher code for wallet balance or course entitlement."""
    now = _utc_now()
    norm = _normalize_code(raw_code)

    # Search by exact code or normalized code
    query = select(Voucher)
    all_vouchers = (await db.scalars(query)).all()
    voucher = None
    for v in all_vouchers:
        if _normalize_code(v.code) == norm:
            voucher = v
            break

    if not voucher:
        raise NotFound("Voucher code")

    # 1. Check if user already redeemed this voucher
    existing_redemption = await db.scalar(
        select(VoucherRedemption).where(
            VoucherRedemption.voucher_id == voucher.id,
            VoucherRedemption.user_id == user_id,
        )
    )
    if existing_redemption:
        raise Conflict(detail="You have already redeemed this voucher code")

    # 2. Check redemption limit / active status
    if not voucher.is_active or voucher.redemptions_count >= voucher.max_redemptions:
        raise Conflict(detail="This voucher code has already reached its maximum redemption limit")

    # 3. Check expiration
    if voucher.expires_at:
        exp = (
            voucher.expires_at
            if voucher.expires_at.tzinfo
            else voucher.expires_at.replace(tzinfo=UTC)
        )
        if exp < now:
            raise ProblemError(
                status=400, title="Expired Voucher", detail="This voucher code has expired"
            )

    credited_egp = None
    new_balance_egp = None
    unlocked_product_id = None
    unlocked_product_title = None

    if voucher.voucher_type == "WALLET_CREDIT":
        # Double-entry ledger credit to user wallet
        user_wallet = await get_or_create_wallet(db, user_id)
        clearing_account = await get_or_create_system_clearing_account(db)

        tx = LedgerTransaction(
            idempotency_key=f"voucher-redeem-{voucher.id}-{user_id}-{uuid.uuid4().hex[:8]}",
            reference=f"VOUCHER-{voucher.code}",
            description=f"Voucher redemption: {voucher.code}",
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
            amount_piastres=voucher.credit_piastres,
            created_at=now,
        )
        credit_entry = LedgerEntry(
            transaction_id=tx.id,
            account_id=user_wallet.id,
            direction="CREDIT",
            amount_piastres=voucher.credit_piastres,
            created_at=now,
        )
        db.add(debit_entry)
        db.add(credit_entry)
        await db.flush()

        credited_egp = piastres_to_egp(voucher.credit_piastres)
        balance_piastres = await calculate_wallet_balance(db, user_wallet.id)
        new_balance_egp = piastres_to_egp(balance_piastres)
        message = (
            f"Voucher redeemed successfully! {format_egp(voucher.credit_piastres)} "
            "added to your wallet."
        )

    elif voucher.voucher_type == "COURSE_UNLOCK":
        product = await db.scalar(select(Product).where(Product.id == voucher.product_id))
        if not product:
            raise NotFound("Entitled course")

        # Grant active entitlement
        ent = await db.scalar(
            select(Entitlement).where(
                Entitlement.user_id == user_id,
                Entitlement.product_id == product.id,
                Entitlement.status == "ACTIVE",
            )
        )
        if not ent:
            ent = Entitlement(
                user_id=user_id,
                product_id=product.id,
                status="ACTIVE",
                granted_at=now,
            )
            db.add(ent)
            await db.flush()

        unlocked_product_id = product.id
        unlocked_product_title = product.title
        message = f"Voucher redeemed! You now have full access to '{product.title}'."
    else:
        raise ProblemError(
            status=400,
            title="Unsupported Voucher Type",
            detail=f"Type {voucher.voucher_type} not supported",
        )

    # Record redemption
    voucher.redemptions_count += 1
    if voucher.redemptions_count >= voucher.max_redemptions:
        voucher.is_active = False

    redemption = VoucherRedemption(
        voucher_id=voucher.id,
        user_id=user_id,
        redeemed_at=now,
        ip_address=ip_address,
    )
    db.add(redemption)
    await db.commit()

    await record_audit_log(
        db,
        action="VOUCHER_REDEEMED",
        resource_type="Voucher",
        resource_id=str(voucher.id),
        actor_id=user_id,
        actor_role="USER",
        ip_address=ip_address,
        user_agent=user_agent,
        details={
            "code": voucher.code,
            "voucher_type": voucher.voucher_type,
            "credited_egp": credited_egp,
            "unlocked_product_id": str(unlocked_product_id) if unlocked_product_id else None,
        },
    )

    return RedeemVoucherResponse(
        success=True,
        voucher_type=voucher.voucher_type,
        message=message,
        credited_egp=credited_egp,
        new_balance_egp=new_balance_egp,
        unlocked_product_id=unlocked_product_id,
        unlocked_product_title=unlocked_product_title,
    )


async def list_vouchers(
    db: AsyncSession,
    limit: int = 100,
) -> list[VoucherOut]:
    """Admin endpoint to inspect recent vouchers."""
    query = select(Voucher).order_by(Voucher.created_at.desc()).limit(limit)
    vouchers = (await db.scalars(query)).all()
    return [
        VoucherOut(
            id=v.id,
            code=v.code,
            voucher_type=v.voucher_type,
            credit_piastres=v.credit_piastres,
            credit_egp=piastres_to_egp(v.credit_piastres),
            product_id=v.product_id,
            max_redemptions=v.max_redemptions,
            redemptions_count=v.redemptions_count,
            is_active=v.is_active,
            expires_at=v.expires_at,
            created_at=v.created_at,
        )
        for v in vouchers
    ]
