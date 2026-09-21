"""Analytics & Audit API router.

User endpoints:
  POST /analytics/pdf-read          → record a PDF open event

Admin endpoints:
  GET  /admin/analytics/pdf-readers → PDF reader report (who read what)
  GET  /admin/analytics/wallet-balances → all user wallet balances
  GET  /admin/analytics/user-wallet/{user_id} → full ledger history for one user
  GET  /admin/analytics/purchases   → combined order+payment purchase history
  GET  /admin/analytics/non-purchasers → users who haven't bought a product
  GET  /admin/audit/events          → immutable audit log query
  GET  /admin/audit/events/{event_id} → single audit log event detail
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Query

from app.common.deps import DbSession
from app.modules.identity.deps import RequireAdmin, RequireUser
from app.modules.analytics.schemas import (
    AuditEventOut,
    AuditLogResponse,
    NonPurchasersReport,
    PdfReadEventRequest,
    PdfReaderReport,
    PurchasesReport,
    UserLedgerHistory,
    WalletBalancesReport,
)
from app.modules.analytics.service import (
    get_user_ledger_history,
    list_audit_events,
    list_non_purchasers,
    list_purchases,
    list_reader_analytics,
    list_wallet_balances,
    upsert_read_event,
)
from app.modules.audit.models import AuditLog
from sqlalchemy import select

router = APIRouter(tags=["analytics"])


# ── User endpoint ─────────────────────────────────────────────────────────────

@router.post("/analytics/pdf-read", status_code=200)
async def record_pdf_read(
    payload: PdfReadEventRequest,
    db: DbSession,
    current_user: RequireUser,
) -> dict:
    """Record that the authenticated user opened a PDF product."""
    await upsert_read_event(
        db,
        user_id=current_user.id,
        product_id=payload.product_id,
        pages_viewed=payload.pages_viewed,
    )
    return {"recorded": True}


# ── Admin: Reader analytics ───────────────────────────────────────────────────

@router.get("/admin/analytics/pdf-readers", response_model=PdfReaderReport)
async def admin_reader_report(
    db: DbSession,
    _admin: RequireAdmin,
    user_id: uuid.UUID | None = Query(default=None),
    product_id: uuid.UUID | None = Query(default=None),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> PdfReaderReport:
    return await list_reader_analytics(
        db,
        user_id=user_id,
        product_id=product_id,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        offset=offset,
    )


# ── Admin: Wallet balances ────────────────────────────────────────────────────

@router.get("/admin/analytics/wallet-balances", response_model=WalletBalancesReport)
async def admin_wallet_balances(
    db: DbSession,
    _admin: RequireAdmin,
    q: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> WalletBalancesReport:
    return await list_wallet_balances(db, query=q, limit=limit, offset=offset)


# ── Admin: User ledger history ────────────────────────────────────────────────

@router.get("/admin/analytics/user-wallet/{user_id}", response_model=UserLedgerHistory)
async def admin_user_wallet_history(
    user_id: uuid.UUID,
    db: DbSession,
    _admin: RequireAdmin,
) -> UserLedgerHistory:
    return await get_user_ledger_history(db, user_id)


# ── Admin: Purchases ──────────────────────────────────────────────────────────

@router.get("/admin/analytics/purchases", response_model=PurchasesReport)
async def admin_purchases(
    db: DbSession,
    _admin: RequireAdmin,
    user_id: uuid.UUID | None = Query(default=None),
    product_id: uuid.UUID | None = Query(default=None),
    status: str | None = Query(default=None),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> PurchasesReport:
    return await list_purchases(
        db,
        user_id=user_id,
        product_id=product_id,
        status=status,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        offset=offset,
    )


# ── Admin: Non-purchasers ─────────────────────────────────────────────────────

@router.get("/admin/analytics/non-purchasers", response_model=NonPurchasersReport)
async def admin_non_purchasers(
    product_id: uuid.UUID,
    db: DbSession,
    _admin: RequireAdmin,
) -> NonPurchasersReport:
    return await list_non_purchasers(db, product_id)


# ── Admin: Audit log ──────────────────────────────────────────────────────────

@router.get("/admin/audit/events", response_model=AuditLogResponse)
async def admin_audit_log(
    db: DbSession,
    _admin: RequireAdmin,
    actor_id: uuid.UUID | None = Query(default=None),
    action: str | None = Query(default=None),
    resource_type: str | None = Query(default=None),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
) -> AuditLogResponse:
    return await list_audit_events(
        db,
        actor_id=actor_id,
        action=action,
        resource_type=resource_type,
        date_from=date_from,
        date_to=date_to,
        page=page,
        page_size=page_size,
    )


@router.get("/admin/audit/events/{event_id}", response_model=AuditEventOut)
async def admin_audit_event_detail(
    event_id: uuid.UUID,
    db: DbSession,
    _admin: RequireAdmin,
) -> AuditEventOut:
    from app.modules.identity.models import User
    from sqlalchemy.ext.asyncio import AsyncSession

    row = await db.execute(
        select(AuditLog, User).outerjoin(User, AuditLog.actor_id == User.id)
        .where(AuditLog.id == event_id)
    )
    result = row.first()
    if not result:
        from app.core.errors import NotFound
        raise NotFound(f"Audit event {event_id}")
    log, usr = result
    return AuditEventOut(
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
