"""Wallet and double-entry ledger domain service (§7, §8, §9, §10, §35).

Strictly enforces:
- All balance adjustments are balanced double-entry ledger transactions.
- No direct balance column or mutable balances.
- Idempotent and auditable manual Vodafone Cash funding lifecycle.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, NotFound, ProblemError
from app.core.money import egp_to_piastres, format_egp
from app.modules.audit.service import record_audit_log
from app.modules.wallet.models import Deposit, LedgerEntry, LedgerTransaction, WalletAccount
from app.modules.wallet.schemas import DepositRequest, DepositResponse, WalletBalanceResponse


def _utc_now() -> datetime:
    return datetime.now(UTC)


async def get_or_create_wallet(
    db: AsyncSession,
    user_id: uuid.UUID,
    currency: str = "EGP",
) -> WalletAccount:
    """Retrieve or initialize a user's wallet account."""
    query = select(WalletAccount).where(
        WalletAccount.user_id == user_id,
        WalletAccount.currency == currency,
    )
    account = await db.scalar(query)
    if not account:
        account = WalletAccount(
            user_id=user_id,
            account_type="USER_WALLET",
            currency=currency,
        )
        db.add(account)
        await db.flush()
    return account


async def get_or_create_system_clearing_account(
    db: AsyncSession,
    currency: str = "EGP",
) -> WalletAccount:
    """Retrieve or initialize the platform cash clearing account."""
    query = select(WalletAccount).where(
        WalletAccount.user_id.is_(None),
        WalletAccount.account_type == "SYSTEM_CLEARING",
        WalletAccount.currency == currency,
    )
    account = await db.scalar(query)
    if not account:
        account = WalletAccount(
            user_id=None,
            account_type="SYSTEM_CLEARING",
            currency=currency,
        )
        db.add(account)
        await db.flush()
    return account


async def get_or_create_system_revenue_account(
    db: AsyncSession,
    currency: str = "EGP",
) -> WalletAccount:
    """Retrieve or initialize the platform revenue account (§8, §10)."""
    query = select(WalletAccount).where(
        WalletAccount.user_id.is_(None),
        WalletAccount.account_type == "SYSTEM_REVENUE",
        WalletAccount.currency == currency,
    )
    account = await db.scalar(query)
    if not account:
        account = WalletAccount(
            user_id=None,
            account_type="SYSTEM_REVENUE",
            currency=currency,
        )
        db.add(account)
        await db.flush()
    return account


async def calculate_wallet_balance(
    db: AsyncSession,
    account_id: uuid.UUID,
) -> int:
    """Derive authoritative balance directly from posted ledger entries (§8)."""
    # Sum credits
    credit_query = (
        select(func.coalesce(func.sum(LedgerEntry.amount_piastres), 0))
        .join(LedgerTransaction)
        .where(
            LedgerEntry.account_id == account_id,
            LedgerEntry.direction == "CREDIT",
            LedgerTransaction.status == "POSTED",
        )
    )
    total_credits = await db.scalar(credit_query) or 0

    # Sum debits
    debit_query = (
        select(func.coalesce(func.sum(LedgerEntry.amount_piastres), 0))
        .join(LedgerTransaction)
        .where(
            LedgerEntry.account_id == account_id,
            LedgerEntry.direction == "DEBIT",
            LedgerTransaction.status == "POSTED",
        )
    )
    total_debits = await db.scalar(debit_query) or 0

    return int(total_credits - total_debits)


async def get_user_balance(
    db: AsyncSession,
    user_id: uuid.UUID,
    currency: str = "EGP",
) -> WalletBalanceResponse:
    """Fetch user balance computed from double-entry ledger."""
    wallet = await get_or_create_wallet(db, user_id, currency=currency)
    balance_piastres = await calculate_wallet_balance(db, wallet.id)
    return WalletBalanceResponse(
        user_id=user_id,
        currency=currency,
        balance_piastres=balance_piastres,
        balance_egp=balance_piastres / 100.0,
        formatted=format_egp(balance_piastres),  # type: ignore[arg-type]
    )


async def submit_deposit(
    db: AsyncSession,
    user_id: uuid.UUID,
    request: DepositRequest,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> DepositResponse:
    """Submit a manual Vodafone Cash deposit request for admin verification (§10)."""
    # Check if reference code already submitted
    existing = await db.scalar(
        select(Deposit).where(Deposit.reference_code == request.reference_code.strip())
    )
    if existing:
        raise Conflict(f"Reference code '{request.reference_code}' has already been submitted.")

    piastres = egp_to_piastres(request.amount_egp)
    deposit = Deposit(
        user_id=user_id,
        amount_piastres=int(piastres),
        method="vodafone_cash",
        sender_phone=request.sender_phone.strip(),
        reference_code=request.reference_code.strip(),
        status="PENDING",
    )
    db.add(deposit)
    await db.flush()

    await record_audit_log(
        db,
        action="wallet.deposit_submitted",
        resource_type="deposit",
        actor_id=user_id,
        resource_id=str(deposit.id),
        details={
            "amount_egp": request.amount_egp,
            "reference_code": deposit.reference_code,
            "sender_phone": deposit.sender_phone,
        },
        ip_address=ip_address,
        user_agent=user_agent,
    )

    await db.commit()
    await db.refresh(deposit)

    return DepositResponse(
        id=deposit.id,
        user_id=deposit.user_id,
        amount_piastres=deposit.amount_piastres,
        amount_egp=deposit.amount_piastres / 100.0,
        method=deposit.method,
        sender_phone=deposit.sender_phone,
        reference_code=deposit.reference_code,
        status=deposit.status,
        created_at=deposit.created_at,
    )


async def approve_deposit(
    db: AsyncSession,
    deposit_id: uuid.UUID,
    admin_id: uuid.UUID,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> DepositResponse:
    """Admin verifies and posts double-entry transaction to fund wallet (§8, §10)."""
    deposit = await db.scalar(select(Deposit).where(Deposit.id == deposit_id))
    if not deposit:
        raise NotFound("Deposit")

    if deposit.status != "PENDING":
        raise ProblemError(
            status_code=400,
            code="validation_error",
            detail=f"Deposit is already in '{deposit.status}' state.",
        )

    # 1. Load accounts
    user_wallet = await get_or_create_wallet(db, deposit.user_id)
    clearing_account = await get_or_create_system_clearing_account(db)

    # 2. Create balanced double-entry transaction
    now = _utc_now()
    tx = LedgerTransaction(
        reference=f"DEP-{deposit.reference_code}",
        description=f"Vodafone Cash deposit via {deposit.sender_phone}",
        status="POSTED",
        created_at=now,
        posted_at=now,
    )
    db.add(tx)
    await db.flush()

    # Double-entry entries: Debit Clearing, Credit User Wallet
    clearing_entry = LedgerEntry(
        transaction_id=tx.id,
        account_id=clearing_account.id,
        direction="DEBIT",
        amount_piastres=deposit.amount_piastres,
        created_at=now,
    )
    user_entry = LedgerEntry(
        transaction_id=tx.id,
        account_id=user_wallet.id,
        direction="CREDIT",
        amount_piastres=deposit.amount_piastres,
        created_at=now,
    )
    db.add_all([clearing_entry, user_entry])

    # 3. Update deposit record
    deposit.status = "APPROVED"
    deposit.transaction_id = tx.id
    deposit.reviewed_by = admin_id
    deposit.reviewed_at = now

    await record_audit_log(
        db,
        action="wallet.deposit_approved",
        resource_type="deposit",
        actor_id=admin_id,
        actor_role="ADMIN",
        resource_id=str(deposit.id),
        details={
            "transaction_id": str(tx.id),
            "amount_piastres": deposit.amount_piastres,
            "user_id": str(deposit.user_id),
        },
        ip_address=ip_address,
        user_agent=user_agent,
    )

    await db.commit()
    await db.refresh(deposit)

    return DepositResponse(
        id=deposit.id,
        user_id=deposit.user_id,
        amount_piastres=deposit.amount_piastres,
        amount_egp=deposit.amount_piastres / 100.0,
        method=deposit.method,
        sender_phone=deposit.sender_phone,
        reference_code=deposit.reference_code,
        status=deposit.status,
        created_at=deposit.created_at,
        reviewed_at=deposit.reviewed_at,
    )


async def reject_deposit(
    db: AsyncSession,
    deposit_id: uuid.UUID,
    admin_id: uuid.UUID,
    reason: str,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> DepositResponse:
    """Admin rejects invalid or fraudulent deposit request."""
    deposit = await db.scalar(select(Deposit).where(Deposit.id == deposit_id))
    if not deposit:
        raise NotFound("Deposit")

    if deposit.status != "PENDING":
        raise ProblemError(
            status_code=400,
            code="validation_error",
            detail=f"Deposit is already in '{deposit.status}' state.",
        )

    now = _utc_now()
    deposit.status = "REJECTED"
    deposit.reviewed_by = admin_id
    deposit.reviewed_at = now
    deposit.rejection_reason = reason

    await record_audit_log(
        db,
        action="wallet.deposit_rejected",
        resource_type="deposit",
        actor_id=admin_id,
        actor_role="ADMIN",
        resource_id=str(deposit.id),
        details={"reason": reason, "user_id": str(deposit.user_id)},
        ip_address=ip_address,
        user_agent=user_agent,
    )

    await db.commit()
    await db.refresh(deposit)

    return DepositResponse(
        id=deposit.id,
        user_id=deposit.user_id,
        amount_piastres=deposit.amount_piastres,
        amount_egp=deposit.amount_piastres / 100.0,
        method=deposit.method,
        sender_phone=deposit.sender_phone,
        reference_code=deposit.reference_code,
        status=deposit.status,
        created_at=deposit.created_at,
        reviewed_at=deposit.reviewed_at,
        rejection_reason=deposit.rejection_reason,
    )


async def admin_manual_user_topup(
    db: AsyncSession,
    admin_id: uuid.UUID,
    user_identifier: str,
    amount_egp: float,
    note: str = "Admin manual top-up",
    ip_address: str | None = None,
    user_agent: str | None = None,
):
    """Directly credit a student's wallet with authoritative double-entry transaction."""
    from app.modules.identity.models import User
    from app.modules.wallet.schemas import AdminManualTopUpResponse

    ident = user_identifier.strip()
    target_user = None
    try:
        u_id = uuid.UUID(ident)
        target_user = await db.get(User, u_id)
    except ValueError:
        pass

    if not target_user:
        q = select(User).where((User.email == ident) | (User.phone == ident))
        target_user = await db.scalar(q)

    if not target_user:
        raise NotFound(f"User with identifier '{user_identifier}'")

    amount_piastres = int(round(amount_egp * 100))
    if amount_piastres <= 0:
        raise ProblemError(status_code=400, code="invalid_amount", detail="Amount must be greater than 0")

    system_clearing = await get_or_create_system_clearing_account(db)
    user_wallet = await get_or_create_wallet(db, target_user.id)

    now = _utc_now()
    tx = LedgerTransaction(
        id=uuid.uuid4(),
        reference=f"TOPUP-{uuid.uuid4().hex[:8].upper()}",
        status="POSTED",
        description=f"Manual Vodafone Cash Top-Up: {note}",
        created_at=now,
        posted_at=now,
    )
    db.add(tx)
    await db.flush()

    debit_entry = LedgerEntry(
        id=uuid.uuid4(),
        transaction_id=tx.id,
        account_id=system_clearing.id,
        direction="DEBIT",
        amount_piastres=amount_piastres,
    )
    credit_entry = LedgerEntry(
        id=uuid.uuid4(),
        transaction_id=tx.id,
        account_id=user_wallet.id,
        direction="CREDIT",
        amount_piastres=amount_piastres,
    )
    db.add_all([debit_entry, credit_entry])
    await db.flush()

    new_balance_piastres = await calculate_wallet_balance(db, user_wallet.id)
    new_balance_egp = new_balance_piastres / 100.0

    await record_audit_log(
        db,
        action="wallet.admin_manual_topup",
        resource_type="wallet",
        actor_id=admin_id,
        actor_role="ADMIN",
        resource_id=str(user_wallet.id),
        details={
            "target_user_id": str(target_user.id),
            "target_email": target_user.email,
            "amount_egp": amount_egp,
            "amount_piastres": amount_piastres,
            "note": note,
            "transaction_id": str(tx.id),
        },
        ip_address=ip_address,
        user_agent=user_agent,
    )

    await db.commit()

    return AdminManualTopUpResponse(
        user_id=target_user.id,
        email=target_user.email,
        amount_egp=amount_egp,
        new_balance_egp=new_balance_egp,
        transaction_id=tx.id,
        message=f"Successfully credited {amount_egp:.2f} EGP to {target_user.email}.",
    )
