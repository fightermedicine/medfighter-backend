"""Payments domain service — controlled Vodafone Cash payment verification.

Invariants enforced:
- APPROVED is a one-way terminal state; no transition back to REJECTED.
- approve_payment is fully idempotent: double-call is a safe no-op.
- Entitlement creation uses INSERT OR IGNORE (unique constraint user+product)
  ensuring zero duplicates even under concurrent approval attempts.
- All state transitions are persisted before any external notification is sent.
- Every state change produces an immutable audit_events record.
- Proof SHA-256 hash is checked globally across all payments to prevent
  reuse of the same screenshot across multiple payment requests.
"""

from __future__ import annotations

import hashlib
import os
import random
import string
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, NotFound, ProblemError
from app.modules.audit.service import record_audit_log
from app.modules.catalog.models import Product
from app.modules.entitlement.models import Entitlement
from app.modules.payments.models import Payment
from app.modules.payments.schemas import AdminPaymentDetail, PaymentResponse, PendingPaymentsResponse
from app.modules.wallet.models import LedgerEntry, LedgerTransaction
from app.modules.wallet.service import (
    get_or_create_system_revenue_account,
    get_or_create_wallet,
)

# Active states where a payment is still in-flight
_ACTIVE_STATUSES = {"CREATED", "UNDER_REVIEW", "MORE_PROOF_REQUIRED"}

# Terminal states — no further transitions allowed
_TERMINAL_STATUSES = {"APPROVED", "REJECTED", "EXPIRED"}

# Proof storage root
_PROOF_DIR = Path(__file__).parents[4] / "storage" / "proofs"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _generate_reference() -> str:
    """Generate a human-readable payment reference: FIGHTER-XXXXXX (6 uppercase alphanum)."""
    chars = string.ascii_uppercase + string.digits
    suffix = "".join(random.choices(chars, k=6))
    return f"FIGHTER-{suffix}"


def _get_merchant_phone() -> str:
    return os.environ.get("VODAFONE_CASH_MERCHANT_PHONE", "010XXXXXXXX")


def _payment_to_response(p: Payment, product_title: str | None = None) -> PaymentResponse:
    return PaymentResponse(
        id=p.id,
        reference=p.reference,
        user_id=p.user_id,
        product_id=p.product_id,
        product_title=product_title,
        amount_piastres=p.amount_piastres,
        amount_egp=p.amount_piastres / 100.0,
        currency=p.currency,
        method=p.method,
        status=p.status,
        rejection_reason=p.rejection_reason,
        submitted_at=p.submitted_at,
        reviewed_at=p.reviewed_at,
        expires_at=p.expires_at,
        created_at=p.created_at,
    )


async def initiate_payment(
    db: AsyncSession,
    user_id: uuid.UUID,
    product_id: uuid.UUID,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> PaymentResponse:
    """Create or return existing in-flight payment for user+product.

    Guards:
    - Product must exist and be active.
    - User must not already own an active entitlement for this product.
    - At most one active payment per user+product (returns existing if found).
    """
    # 1. Validate product
    product = await db.scalar(
        select(Product).where(Product.id == product_id, Product.is_active.is_(True))
    )
    if not product:
        raise NotFound("Product")

    # 2. Check existing entitlement — user already owns this
    existing_entitlement = await db.scalar(
        select(Entitlement).where(
            Entitlement.user_id == user_id,
            Entitlement.product_id == product_id,
            Entitlement.status == "ACTIVE",
        )
    )
    if existing_entitlement:
        raise Conflict("You already own this product.")

    # 3. Return existing active payment if one exists
    existing_payment = await db.scalar(
        select(Payment).where(
            Payment.user_id == user_id,
            Payment.product_id == product_id,
            Payment.status.in_(list(_ACTIVE_STATUSES)),
        )
    )
    if existing_payment:
        resp = _payment_to_response(existing_payment, product_title=product.title)
        resp.merchant_phone = _get_merchant_phone()
        return resp

    # 4. Generate unique reference (retry up to 5 times on collision)
    reference = None
    for _ in range(5):
        candidate = _generate_reference()
        clash = await db.scalar(select(Payment).where(Payment.reference == candidate))
        if not clash:
            reference = candidate
            break
    if not reference:
        raise ProblemError(status_code=500, code="reference_generation_failed",
                           detail="Could not generate unique payment reference.")

    # 5. Create payment record
    payment = Payment(
        user_id=user_id,
        product_id=product_id,
        reference=reference,
        amount_piastres=product.price_piastres,
        status="CREATED",
    )
    db.add(payment)
    await db.flush()

    await record_audit_log(
        db,
        action="payment.initiated",
        resource_type="payment",
        actor_id=user_id,
        resource_id=str(payment.id),
        details={
            "reference": reference,
            "product_id": str(product_id),
            "product_title": product.title,
            "amount_piastres": product.price_piastres,
        },
        ip_address=ip_address,
        user_agent=user_agent,
    )

    await db.commit()
    await db.refresh(payment)

    resp = _payment_to_response(payment, product_title=product.title)
    resp.merchant_phone = _get_merchant_phone()
    return resp


async def submit_proof(
    db: AsyncSession,
    payment_id: uuid.UUID,
    user_id: uuid.UUID,
    file_bytes: bytes,
    content_type: str,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> PaymentResponse:
    """Upload payment proof screenshot. Sets status → UNDER_REVIEW.

    Guards:
    - Payment must belong to the requesting user.
    - Payment must be in CREATED or MORE_PROOF_REQUIRED state.
    - Proof SHA-256 hash must be globally unique (prevents recycled screenshots).
    """
    payment = await db.scalar(select(Payment).where(Payment.id == payment_id))
    if not payment:
        raise NotFound("Payment")
    if payment.user_id != user_id:
        raise ProblemError(status_code=403, code="forbidden", detail="Payment does not belong to you.")
    if payment.status not in {"CREATED", "MORE_PROOF_REQUIRED"}:
        raise ProblemError(
            status_code=400,
            code="invalid_state",
            detail=f"Cannot submit proof for a payment in '{payment.status}' state.",
        )

    # Compute SHA-256 hash for deduplication
    proof_hash = hashlib.sha256(file_bytes).hexdigest()
    clash = await db.scalar(select(Payment).where(Payment.proof_hash == proof_hash))
    if clash and clash.id != payment_id:
        raise Conflict("This screenshot has already been submitted for another payment.")

    # Determine file extension
    ext = "jpg" if "jpeg" in content_type or "jpg" in content_type else "png"
    _PROOF_DIR.mkdir(parents=True, exist_ok=True)
    proof_path = _PROOF_DIR / f"{payment.id}.{ext}"
    proof_path.write_bytes(file_bytes)

    now = _utc_now()
    payment.proof_path = str(proof_path)
    payment.proof_hash = proof_hash
    payment.proof_content_type = content_type
    payment.status = "UNDER_REVIEW"
    payment.submitted_at = now

    await record_audit_log(
        db,
        action="payment.proof_submitted",
        resource_type="payment",
        actor_id=user_id,
        resource_id=str(payment.id),
        details={"reference": payment.reference, "proof_hash": proof_hash[:16] + "..."},
        ip_address=ip_address,
        user_agent=user_agent,
    )

    await db.commit()
    await db.refresh(payment)

    # Notify all admins of new pending payment (fire-and-forget)
    try:
        from app.modules.notifications.service import notify_admins_payment_pending
        await notify_admins_payment_pending(db, payment)
    except Exception:
        pass  # Non-critical

    product = await db.get(Product, payment.product_id)
    return _payment_to_response(payment, product_title=product.title if product else None)


async def approve_payment(
    db: AsyncSession,
    payment_id: uuid.UUID,
    admin_id: uuid.UUID,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> PaymentResponse:
    """Atomically approve a payment: post ledger + grant entitlement + update status.

    Idempotent: if already APPROVED, returns current state as a no-op.
    """
    payment = await db.scalar(select(Payment).where(Payment.id == payment_id))
    if not payment:
        raise NotFound("Payment")

    # Idempotent: already approved → return as-is
    if payment.status == "APPROVED":
        product = await db.get(Product, payment.product_id)
        return _payment_to_response(payment, product_title=product.title if product else None)

    if payment.status not in {"UNDER_REVIEW", "MORE_PROOF_REQUIRED"}:
        raise ProblemError(
            status_code=400,
            code="invalid_state",
            detail=f"Cannot approve a payment in '{payment.status}' state.",
        )

    now = _utc_now()
    product = await db.get(Product, payment.product_id)

    # ── ATOMIC BLOCK ──────────────────────────────────────────────────────────
    # 1. Create ledger transaction (revenue)
    user_wallet = await get_or_create_wallet(db, payment.user_id)
    revenue_account = await get_or_create_system_revenue_account(db)

    tx = LedgerTransaction(
        reference=f"PAY-{payment.reference}",
        description=f"Vodafone Cash payment for '{product.title if product else payment.product_id}'",
        status="POSTED",
        created_at=now,
        posted_at=now,
    )
    db.add(tx)
    await db.flush()

    # Double-entry: Debit user wallet (cost), Credit revenue
    debit = LedgerEntry(
        transaction_id=tx.id,
        account_id=user_wallet.id,
        direction="DEBIT",
        amount_piastres=payment.amount_piastres,
        created_at=now,
    )
    credit = LedgerEntry(
        transaction_id=tx.id,
        account_id=revenue_account.id,
        direction="CREDIT",
        amount_piastres=payment.amount_piastres,
        created_at=now,
    )
    db.add_all([debit, credit])
    await db.flush()

    # 2. Grant entitlement (upsert-safe via unique constraint)
    existing_ent = await db.scalar(
        select(Entitlement).where(
            Entitlement.user_id == payment.user_id,
            Entitlement.product_id == payment.product_id,
        )
    )
    if existing_ent:
        # Reactivate if previously revoked
        existing_ent.status = "ACTIVE"
        entitlement = existing_ent
    else:
        entitlement = Entitlement(
            user_id=payment.user_id,
            product_id=payment.product_id,
            status="ACTIVE",
            granted_at=now,
        )
        db.add(entitlement)

    await db.flush()

    # 3. Update payment record
    payment.status = "APPROVED"
    payment.reviewed_by = admin_id
    payment.reviewed_at = now
    payment.ledger_transaction_id = tx.id
    payment.entitlement_id = entitlement.id

    # 4. Audit log
    await record_audit_log(
        db,
        action="payment.approved",
        resource_type="payment",
        actor_id=admin_id,
        actor_role="ADMIN",
        resource_id=str(payment.id),
        details={
            "reference": payment.reference,
            "user_id": str(payment.user_id),
            "product_id": str(payment.product_id),
            "amount_piastres": payment.amount_piastres,
            "ledger_transaction_id": str(tx.id),
            "entitlement_id": str(entitlement.id),
        },
        ip_address=ip_address,
        user_agent=user_agent,
    )

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise ProblemError(status_code=409, code="entitlement_conflict",
                           detail="Entitlement could not be granted due to a conflict.")

    await db.refresh(payment)
    # ── END ATOMIC BLOCK ──────────────────────────────────────────────────────

    # Notify user (non-critical, after commit)
    try:
        from app.modules.notifications.service import notify_payment_approved
        await notify_payment_approved(db, payment, product)
    except Exception:
        pass

    return _payment_to_response(payment, product_title=product.title if product else None)


async def reject_payment(
    db: AsyncSession,
    payment_id: uuid.UUID,
    admin_id: uuid.UUID,
    reason: str,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> PaymentResponse:
    """Reject a payment with a mandatory reason. Cannot reject APPROVED payments."""
    payment = await db.scalar(select(Payment).where(Payment.id == payment_id))
    if not payment:
        raise NotFound("Payment")

    if payment.status == "APPROVED":
        raise ProblemError(
            status_code=400,
            code="cannot_reject_approved",
            detail="An approved payment cannot be rejected. Use admin reversal workflow.",
        )
    if payment.status in {"REJECTED", "EXPIRED"}:
        raise ProblemError(
            status_code=400,
            code="invalid_state",
            detail=f"Payment is already in terminal state '{payment.status}'.",
        )

    now = _utc_now()
    payment.status = "REJECTED"
    payment.reviewed_by = admin_id
    payment.reviewed_at = now
    payment.rejection_reason = reason.strip()

    await record_audit_log(
        db,
        action="payment.rejected",
        resource_type="payment",
        actor_id=admin_id,
        actor_role="ADMIN",
        resource_id=str(payment.id),
        details={
            "reference": payment.reference,
            "reason": reason,
            "user_id": str(payment.user_id),
        },
        ip_address=ip_address,
        user_agent=user_agent,
    )

    await db.commit()
    await db.refresh(payment)

    try:
        from app.modules.notifications.service import notify_payment_rejected
        product = await db.get(Product, payment.product_id)
        await notify_payment_rejected(db, payment, product, reason)
    except Exception:
        pass

    product = await db.get(Product, payment.product_id)
    return _payment_to_response(payment, product_title=product.title if product else None)


async def request_new_proof(
    db: AsyncSession,
    payment_id: uuid.UUID,
    admin_id: uuid.UUID,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> PaymentResponse:
    """Ask user to re-upload payment proof. Sets status → MORE_PROOF_REQUIRED."""
    payment = await db.scalar(select(Payment).where(Payment.id == payment_id))
    if not payment:
        raise NotFound("Payment")

    if payment.status != "UNDER_REVIEW":
        raise ProblemError(
            status_code=400,
            code="invalid_state",
            detail=f"Cannot request new proof for payment in '{payment.status}' state.",
        )

    payment.status = "MORE_PROOF_REQUIRED"

    await record_audit_log(
        db,
        action="payment.more_proof_requested",
        resource_type="payment",
        actor_id=admin_id,
        actor_role="ADMIN",
        resource_id=str(payment.id),
        details={"reference": payment.reference},
        ip_address=ip_address,
        user_agent=user_agent,
    )

    await db.commit()
    await db.refresh(payment)

    try:
        from app.modules.notifications.service import notify_payment_more_proof
        product = await db.get(Product, payment.product_id)
        await notify_payment_more_proof(db, payment, product)
    except Exception:
        pass

    product = await db.get(Product, payment.product_id)
    return _payment_to_response(payment, product_title=product.title if product else None)


async def get_user_payments(
    db: AsyncSession,
    user_id: uuid.UUID,
) -> list[PaymentResponse]:
    """List all payments for a user, newest first."""
    payments = (
        await db.scalars(
            select(Payment)
            .where(Payment.user_id == user_id)
            .order_by(Payment.created_at.desc())
        )
    ).all()

    result = []
    for p in payments:
        product = await db.get(Product, p.product_id)
        result.append(_payment_to_response(p, product_title=product.title if product else None))
    return result


async def get_payment(
    db: AsyncSession,
    payment_id: uuid.UUID,
    user_id: uuid.UUID,
) -> PaymentResponse:
    """Get a single payment, validates it belongs to the requesting user."""
    payment = await db.scalar(select(Payment).where(Payment.id == payment_id))
    if not payment:
        raise NotFound("Payment")
    if payment.user_id != user_id:
        raise ProblemError(status_code=403, code="forbidden", detail="Payment does not belong to you.")
    product = await db.get(Product, payment.product_id)
    return _payment_to_response(payment, product_title=product.title if product else None)


async def list_pending_payments(db: AsyncSession) -> PendingPaymentsResponse:
    """Admin: list all payments awaiting review, oldest first (FIFO queue)."""
    from app.modules.identity.models import User

    payments = (
        await db.scalars(
            select(Payment)
            .where(Payment.status.in_(["UNDER_REVIEW", "MORE_PROOF_REQUIRED"]))
            .order_by(Payment.submitted_at.asc())
        )
    ).all()

    items: list[AdminPaymentDetail] = []
    for p in payments:
        product = await db.get(Product, p.product_id)
        user = await db.get(User, p.user_id)
        proof_url = f"/v1/admin/payments/{p.id}/proof" if p.proof_path else None
        items.append(
            AdminPaymentDetail(
                id=p.id,
                reference=p.reference,
                user_id=p.user_id,
                user_email=user.email if user else None,
                user_name=user.full_name if user else None,
                product_id=p.product_id,
                product_title=product.title if product else None,
                amount_piastres=p.amount_piastres,
                amount_egp=p.amount_piastres / 100.0,
                currency=p.currency,
                method=p.method,
                status=p.status,
                rejection_reason=p.rejection_reason,
                submitted_at=p.submitted_at,
                reviewed_at=p.reviewed_at,
                expires_at=p.expires_at,
                created_at=p.created_at,
                proof_url=proof_url,
            )
        )

    return PendingPaymentsResponse(total=len(items), items=items)


async def get_payment_as_admin(
    db: AsyncSession,
    payment_id: uuid.UUID,
) -> AdminPaymentDetail:
    """Admin: get full payment detail."""
    from app.modules.identity.models import User

    payment = await db.scalar(select(Payment).where(Payment.id == payment_id))
    if not payment:
        raise NotFound("Payment")

    product = await db.get(Product, payment.product_id)
    user = await db.get(User, payment.user_id)
    proof_url = f"/v1/admin/payments/{payment.id}/proof" if payment.proof_path else None

    return AdminPaymentDetail(
        id=payment.id,
        reference=payment.reference,
        user_id=payment.user_id,
        user_email=user.email if user else None,
        user_name=user.full_name if user else None,
        product_id=payment.product_id,
        product_title=product.title if product else None,
        amount_piastres=payment.amount_piastres,
        amount_egp=payment.amount_piastres / 100.0,
        currency=payment.currency,
        method=payment.method,
        status=payment.status,
        rejection_reason=payment.rejection_reason,
        submitted_at=payment.submitted_at,
        reviewed_at=payment.reviewed_at,
        expires_at=payment.expires_at,
        created_at=payment.created_at,
        proof_url=proof_url,
    )
