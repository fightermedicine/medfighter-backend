"""Analytics domain service.

All functions are read-only SELECT queries except upsert_read_event which is
an atomic INSERT ... ON CONFLICT DO UPDATE (PostgreSQL dialect).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import piastres_to_egp
from app.modules.analytics.models import PdfReadEvent
from app.modules.analytics.schemas import (
    AuditEventOut,
    AuditLogResponse,
    LedgerHistoryRow,
    MemoAccessItemOut,
    MemoAccessReport,
    MemoStudentAccessOut,
    NonPurchaserRow,
    NonPurchasersReport,
    PdfReaderReport,
    PdfReaderRow,
    PurchaseRow,
    PurchasesReport,
    UserLedgerHistory,
    WalletBalanceRow,
    WalletBalancesReport,
)
from app.modules.audit.models import AuditLog
from app.modules.catalog.models import Product
from app.modules.entitlement.models import Entitlement
from app.modules.identity.models import User
from app.modules.orders.models import Order
from app.modules.payments.models import Payment
from app.modules.wallet.models import LedgerEntry, LedgerTransaction, WalletAccount
from app.modules.wallet.service import calculate_wallet_balance


def _utc_now() -> datetime:
    return datetime.now(UTC)


# ── PDF Read Event ────────────────────────────────────────────────────────────

async def upsert_read_event(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    product_id: uuid.UUID,
    pages_viewed: int = 0,
) -> None:
    """Upsert: increment open_count, update pages_viewed and last_opened_at atomically."""
    now = _utc_now()

    # Try to get existing row
    existing = await db.scalar(
        select(PdfReadEvent).where(
            PdfReadEvent.user_id == user_id,
            PdfReadEvent.product_id == product_id,
        )
    )

    if existing:
        existing.open_count += 1
        existing.last_opened_at = now
        if pages_viewed > existing.pages_viewed:
            existing.pages_viewed = pages_viewed
    else:
        event = PdfReadEvent(
            user_id=user_id,
            product_id=product_id,
            open_count=1,
            pages_viewed=pages_viewed,
            first_opened_at=now,
            last_opened_at=now,
        )
        db.add(event)

    await db.commit()


# ── Reader Analytics Report ───────────────────────────────────────────────────

async def list_reader_analytics(
    db: AsyncSession,
    *,
    user_id: uuid.UUID | None = None,
    product_id: uuid.UUID | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    limit: int = 200,
    offset: int = 0,
) -> PdfReaderReport:
    """Admin report: who opened which PDF, how many times, pages viewed."""
    stmt = (
        select(PdfReadEvent, User, Product)
        .join(User, PdfReadEvent.user_id == User.id)
        .join(Product, PdfReadEvent.product_id == Product.id)
    )

    if user_id:
        stmt = stmt.where(PdfReadEvent.user_id == user_id)
    if product_id:
        stmt = stmt.where(PdfReadEvent.product_id == product_id)
    if date_from:
        stmt = stmt.where(PdfReadEvent.last_opened_at >= date_from)
    if date_to:
        stmt = stmt.where(PdfReadEvent.last_opened_at <= date_to)

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await db.scalar(count_stmt)) or 0

    stmt = stmt.order_by(PdfReadEvent.last_opened_at.desc()).limit(limit).offset(offset)
    rows = (await db.execute(stmt)).all()

    items = [
        PdfReaderRow(
            product_id=evt.product_id,
            product_title=prod.title,
            user_id=evt.user_id,
            user_name=usr.full_name,
            user_email=usr.email,
            open_count=evt.open_count,
            pages_viewed=evt.pages_viewed,
            first_opened_at=evt.first_opened_at,
            last_opened_at=evt.last_opened_at,
        )
        for evt, usr, prod in rows
    ]
    return PdfReaderReport(total=total, items=items)


# ── Finance: Wallet Balances ──────────────────────────────────────────────────

async def list_wallet_balances(
    db: AsyncSession,
    *,
    query: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> WalletBalancesReport:
    """List all user wallets with current balance (dynamically derived from ledger)."""
    user_stmt = select(User).where(User.is_active.is_(True))
    if query and query.strip():
        q = f"%{query.strip()}%"
        user_stmt = user_stmt.where(
            or_(User.full_name.ilike(q), User.email.ilike(q))
        )

    count = (await db.scalar(select(func.count()).select_from(user_stmt.subquery()))) or 0
    users = (await db.scalars(user_stmt.order_by(User.full_name).limit(limit).offset(offset))).all()

    if not users:
        return WalletBalancesReport(total=count, items=[])

    user_ids = [u.id for u in users]
    wallets = (
        await db.scalars(
            select(WalletAccount).where(
                WalletAccount.user_id.in_(user_ids),
                WalletAccount.currency == "EGP",
            )
        )
    ).all()
    wallet_map = {w.user_id: w for w in wallets if w.user_id}

    # Compute balances in bulk
    account_ids = [w.id for w in wallet_map.values()]
    bal_map: dict[uuid.UUID, int] = {aid: 0 for aid in account_ids}
    if account_ids:
        cr = await db.execute(
            select(LedgerEntry.account_id, func.coalesce(func.sum(LedgerEntry.amount_piastres), 0))
            .join(LedgerTransaction, LedgerEntry.transaction_id == LedgerTransaction.id)
            .where(
                LedgerEntry.account_id.in_(account_ids),
                LedgerEntry.direction == "CREDIT",
                LedgerTransaction.status == "POSTED",
            )
            .group_by(LedgerEntry.account_id)
        )
        dr = await db.execute(
            select(LedgerEntry.account_id, func.coalesce(func.sum(LedgerEntry.amount_piastres), 0))
            .join(LedgerTransaction, LedgerEntry.transaction_id == LedgerTransaction.id)
            .where(
                LedgerEntry.account_id.in_(account_ids),
                LedgerEntry.direction == "DEBIT",
                LedgerTransaction.status == "POSTED",
            )
            .group_by(LedgerEntry.account_id)
        )
        cr_dict = {r[0]: r[1] for r in cr.all()}
        dr_dict = {r[0]: r[1] for r in dr.all()}
        for aid in account_ids:
            bal_map[aid] = int(cr_dict.get(aid, 0)) - int(dr_dict.get(aid, 0))

    items = []
    for u in users:
        w = wallet_map.get(u.id)
        bal = bal_map.get(w.id, 0) if w else 0
        items.append(
            WalletBalanceRow(
                user_id=u.id,
                user_name=u.full_name,
                user_email=u.email,
                balance_egp=piastres_to_egp(bal),
                account_id=w.id if w else uuid.uuid4(),
            )
        )

    # Sort by balance desc
    items.sort(key=lambda r: r.balance_egp, reverse=True)
    return WalletBalancesReport(total=count, items=items)


# ── Finance: User Ledger History ──────────────────────────────────────────────

async def get_user_ledger_history(
    db: AsyncSession,
    user_id: uuid.UUID,
) -> UserLedgerHistory:
    """Full ledger transaction history for one user."""
    user = await db.scalar(select(User).where(User.id == user_id))
    if not user:
        from app.core.errors import NotFound
        raise NotFound(f"User {user_id}")

    wallet = await db.scalar(
        select(WalletAccount).where(
            WalletAccount.user_id == user_id,
            WalletAccount.currency == "EGP",
        )
    )

    if not wallet:
        return UserLedgerHistory(
            user_id=user_id,
            user_name=user.full_name,
            user_email=user.email,
            balance_egp=0.0,
            transactions=[],
        )

    balance = await calculate_wallet_balance(db, wallet.id)

    rows = (
        await db.execute(
            select(LedgerEntry, LedgerTransaction)
            .join(LedgerTransaction, LedgerEntry.transaction_id == LedgerTransaction.id)
            .where(
                LedgerEntry.account_id == wallet.id,
                LedgerTransaction.status == "POSTED",
            )
            .order_by(LedgerTransaction.posted_at.desc())
            .limit(500)
        )
    ).all()

    txns = [
        LedgerHistoryRow(
            transaction_id=tx.id,
            reference=tx.reference,
            description=tx.description,
            direction=entry.direction,
            amount_egp=piastres_to_egp(int(entry.amount_piastres)),
            status=tx.status,
            posted_at=tx.posted_at,
            created_at=tx.created_at,
        )
        for entry, tx in rows
    ]

    return UserLedgerHistory(
        user_id=user_id,
        user_name=user.full_name,
        user_email=user.email,
        balance_egp=piastres_to_egp(balance),
        transactions=txns,
    )


# ── Finance: Purchases ────────────────────────────────────────────────────────

async def list_purchases(
    db: AsyncSession,
    *,
    user_id: uuid.UUID | None = None,
    product_id: uuid.UUID | None = None,
    status: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    limit: int = 200,
    offset: int = 0,
) -> PurchasesReport:
    """Combined wallet-order + Vodafone Cash payment purchase history."""
    items: list[PurchaseRow] = []

    # 1. Wallet orders (Order table)
    ord_stmt = (
        select(Order, User, Product)
        .join(User, Order.user_id == User.id)
        .join(Product, Order.product_id == Product.id)
    )
    if user_id:
        ord_stmt = ord_stmt.where(Order.user_id == user_id)
    if product_id:
        ord_stmt = ord_stmt.where(Order.product_id == product_id)
    if status:
        ord_stmt = ord_stmt.where(Order.status == status)
    if date_from:
        ord_stmt = ord_stmt.where(Order.created_at >= date_from)
    if date_to:
        ord_stmt = ord_stmt.where(Order.created_at <= date_to)

    order_rows = (await db.execute(ord_stmt)).all()
    for order, usr, prod in order_rows:
        items.append(
            PurchaseRow(
                order_id=order.id,
                user_id=usr.id,
                user_name=usr.full_name,
                user_email=usr.email,
                product_id=prod.id,
                product_title=prod.title,
                quantity=order.quantity,
                amount_egp=piastres_to_egp(int(order.amount_piastres)),
                status=order.status,
                method="wallet",
                purchased_at=order.created_at,
            )
        )

    # 2. Vodafone Cash payments (Payment table — APPROVED only counts as purchase)
    pay_stmt = (
        select(Payment, User, Product)
        .join(User, Payment.user_id == User.id)
        .join(Product, Payment.product_id == Product.id)
    )
    if user_id:
        pay_stmt = pay_stmt.where(Payment.user_id == user_id)
    if product_id:
        pay_stmt = pay_stmt.where(Payment.product_id == product_id)
    if date_from:
        pay_stmt = pay_stmt.where(Payment.created_at >= date_from)
    if date_to:
        pay_stmt = pay_stmt.where(Payment.created_at <= date_to)
    # If status filter: map COMPLETED → APPROVED for payments
    pay_status = status or "APPROVED"
    if status in (None, "COMPLETED", "APPROVED"):
        pay_stmt = pay_stmt.where(Payment.status == "APPROVED")

    pay_rows = (await db.execute(pay_stmt)).all()
    for payment, usr, prod in pay_rows:
        items.append(
            PurchaseRow(
                order_id=payment.id,  # reuse order_id field for payment ID
                user_id=usr.id,
                user_name=usr.full_name,
                user_email=usr.email,
                product_id=prod.id,
                product_title=prod.title,
                quantity=1,
                amount_egp=piastres_to_egp(int(payment.amount_piastres)),
                status="APPROVED",
                method="vodafone_cash",
                purchased_at=payment.reviewed_at or payment.created_at,
            )
        )

    # Sort all by purchased_at desc, then paginate
    items.sort(key=lambda r: r.purchased_at, reverse=True)
    total = len(items)
    page_items = items[offset: offset + limit]
    return PurchasesReport(total=total, items=page_items)


# ── Finance: Non-Purchasers ───────────────────────────────────────────────────

async def list_non_purchasers(
    db: AsyncSession,
    product_id: uuid.UUID,
) -> NonPurchasersReport:
    """Users who have no ACTIVE entitlement for a given product."""
    product = await db.scalar(
        select(Product).options(defer(Product.preview_data)).where(Product.id == product_id)
    )
    if not product:
        from app.core.errors import NotFound
        raise NotFound(f"Product {product_id}")

    total_users = (
        await db.scalar(
            select(func.count(User.id)).where(User.is_active.is_(True))
        )
    ) or 0

    # Query active users without active entitlement directly in PostgreSQL
    non_purchaser_stmt = (
        select(User)
        .outerjoin(
            Entitlement,
            (Entitlement.user_id == User.id)
            & (Entitlement.product_id == product_id)
            & (Entitlement.status == "ACTIVE"),
        )
        .where(
            User.is_active.is_(True),
            Entitlement.id.is_(None),
        )
        .order_by(User.created_at.desc())
    )
    non_purchasers = (await db.scalars(non_purchaser_stmt)).all()

    items = [
        NonPurchaserRow(
            user_id=u.id,
            user_name=u.full_name,
            user_email=u.email,
            medical_year=u.medical_year,
            created_at=u.created_at,
        )
        for u in non_purchasers
    ]

    return NonPurchasersReport(
        product_id=product_id,
        product_title=product.title,
        total_users=total_users,
        non_purchasers=len(items),
        items=items,
    )


# ── Audit Log Query ───────────────────────────────────────────────────────────

async def list_audit_events(
    db: AsyncSession,
    *,
    actor_id: uuid.UUID | None = None,
    action: str | None = None,
    resource_type: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    page: int = 1,
    page_size: int = 50,
) -> AuditLogResponse:
    """Query immutable audit log with filters."""
    stmt = select(AuditLog, User).outerjoin(User, AuditLog.actor_id == User.id)

    if actor_id:
        stmt = stmt.where(AuditLog.actor_id == actor_id)
    if action:
        stmt = stmt.where(AuditLog.action.ilike(f"%{action}%"))
    if resource_type:
        stmt = stmt.where(AuditLog.resource_type.ilike(f"%{resource_type}%"))
    if date_from:
        stmt = stmt.where(AuditLog.created_at >= date_from)
    if date_to:
        stmt = stmt.where(AuditLog.created_at <= date_to)

    count_stmt = select(func.count()).select_from(
        select(AuditLog).where(
            *([AuditLog.actor_id == actor_id] if actor_id else []),
            *([AuditLog.action.ilike(f"%{action}%")] if action else []),
            *([AuditLog.resource_type.ilike(f"%{resource_type}%")] if resource_type else []),
            *([AuditLog.created_at >= date_from] if date_from else []),
            *([AuditLog.created_at <= date_to] if date_to else []),
        ).subquery()
    )
    total = (await db.scalar(count_stmt)) or 0

    offset = (page - 1) * page_size
    stmt = stmt.order_by(AuditLog.created_at.desc()).limit(page_size).offset(offset)
    rows = (await db.execute(stmt)).all()

    items = [
        AuditEventOut(
            id=log.id,
            actor_id=log.actor_id,
            actor_role=log.actor_role,
            actor_name=usr.full_name if usr else None,
            actor_email=usr.email if usr else None,
            action=log.action,
            resource_type=log.resource_type,
            resource_id=log.resource_id,
            details=log.details,
            ip_address=log.ip_address,
            result=log.result,
            created_at=log.created_at,
        )
        for log, usr in rows
    ]

    return AuditLogResponse(total=total, page=page, page_size=page_size, items=items)


# ── Memo Access Analytics ───────────────────────────────────────────────────

async def get_memo_access_report(
    db: AsyncSession,
    *,
    query: str | None = None,
    medical_year: int | None = None,
    product_id: uuid.UUID | None = None,
) -> MemoAccessReport:
    """Return analytics on which students have access to each product/memo."""
    # 1. Query products (select only needed columns to avoid 5-query ORM relation explosion and huge base64 transfers)
    prod_stmt = select(
        Product.id,
        Product.title,
        Product.product_type,
        Product.medical_year,
        Product.price_piastres,
    ).where(Product.is_active.is_(True))
    if product_id:
        prod_stmt = prod_stmt.where(Product.id == product_id)
    if medical_year:
        prod_stmt = prod_stmt.where(Product.medical_year == medical_year)
    if query and query.strip():
        q_str = f"%{query.strip()}%"
        prod_stmt = prod_stmt.where(Product.title.ilike(q_str))

    prod_stmt = prod_stmt.order_by(Product.medical_year.asc(), Product.title.asc())
    products = (await db.execute(prod_stmt)).all()

    if not products:
        return MemoAccessReport(total_products=0, total_active_entitlements=0, items=[])

    prod_ids = [p.id for p in products]

    # 2. Query active entitlements joined with User for these products
    ent_stmt = (
        select(Entitlement, User)
        .join(User, Entitlement.user_id == User.id)
        .where(
            Entitlement.product_id.in_(prod_ids),
            Entitlement.status == "ACTIVE",
            User.is_active.is_(True),
        )
        .order_by(Entitlement.granted_at.desc())
    )
    ent_rows = (await db.execute(ent_stmt)).all()

    # Map entitlements by product_id
    from collections import defaultdict
    students_by_product: dict[uuid.UUID, list[MemoStudentAccessOut]] = defaultdict(list)
    for ent, usr in ent_rows:
        students_by_product[ent.product_id].append(
            MemoStudentAccessOut(
                user_id=usr.id,
                user_name=usr.full_name,
                user_email=usr.email,
                user_phone=usr.phone,
                medical_year=usr.medical_year,
                status=ent.status,
                granted_at=ent.granted_at,
                expires_at=ent.expires_at,
            )
        )

    items: list[MemoAccessItemOut] = []
    total_active_entitlements = 0
    for p in products:
        p_students = students_by_product.get(p.id, [])
        student_count = len(p_students)
        total_active_entitlements += student_count
        items.append(
            MemoAccessItemOut(
                product_id=p.id,
                product_title=p.title,
                product_type=p.product_type,
                medical_year=p.medical_year,
                price_egp=piastres_to_egp(p.price_piastres),
                student_count=student_count,
                students=p_students,
            )
        )

    # Sort items: products with the most students first, then by title
    items.sort(key=lambda x: (x.student_count, x.product_title), reverse=True)

    return MemoAccessReport(
        total_products=len(items),
        total_active_entitlements=total_active_entitlements,
        items=items,
    )
