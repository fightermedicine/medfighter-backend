"""Wallet and Deposit API router (§7, §10, §37).

Endpoints:
- /v1/wallet/balance (Learner)
- /v1/wallet/deposits (Learner)
- /v1/admin/wallet/deposits/{id}/approve (Admin only)
- /v1/admin/wallet/deposits/{id}/reject (Admin only)
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Request, status
from sqlalchemy import select

from app.common.deps import DbSession
from app.modules.identity.deps import RequireAdmin, RequireUser
from app.modules.wallet.models import Deposit
from app.modules.wallet.schemas import (
    AdminManualTopUpRequest,
    AdminManualTopUpResponse,
    DepositRequest,
    DepositResponse,
    RejectDepositRequest,
    WalletBalanceResponse,
)
from app.modules.wallet.service import (
    admin_manual_user_topup,
    approve_deposit,
    get_user_balance,
    reject_deposit,
    submit_deposit,
)

router = APIRouter(tags=["wallet"])


def _extract_client_meta(request: Request) -> tuple[str | None, str | None]:
    ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent")
    return ip, ua


@router.get(
    "/wallet/balance",
    response_model=WalletBalanceResponse,
    summary="Get authenticated user authoritative balance derived from ledger",
)
async def get_balance(
    current_user: RequireUser,
    db: DbSession,
) -> WalletBalanceResponse:
    return await get_user_balance(db, current_user.id)


@router.post(
    "/wallet/deposits",
    response_model=DepositResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Submit manual Vodafone Cash funding reference",
)
async def create_deposit_request(
    request: Request,
    payload: DepositRequest,
    current_user: RequireUser,
    db: DbSession,
) -> DepositResponse:
    ip, ua = _extract_client_meta(request)
    return await submit_deposit(db, current_user.id, payload, ip_address=ip, user_agent=ua)


@router.get(
    "/wallet/deposits",
    response_model=list[DepositResponse],
    summary="List deposits submitted by current user",
)
async def list_user_deposits(
    current_user: RequireUser,
    db: DbSession,
) -> list[DepositResponse]:
    query = (
        select(Deposit)
        .where(Deposit.user_id == current_user.id)
        .order_by(Deposit.created_at.desc())
    )
    deposits = (await db.scalars(query)).all()
    return [
        DepositResponse(
            id=d.id,
            user_id=d.user_id,
            amount_piastres=d.amount_piastres,
            amount_egp=d.amount_piastres / 100.0,
            method=d.method,
            sender_phone=d.sender_phone,
            reference_code=d.reference_code,
            status=d.status,
            created_at=d.created_at,
            reviewed_at=d.reviewed_at,
            rejection_reason=d.rejection_reason,
        )
        for d in deposits
    ]


@router.post(
    "/admin/wallet/deposits/{deposit_id}/approve",
    response_model=DepositResponse,
    summary="Admin approves deposit and posts double-entry transaction (§7, §10)",
)
async def admin_approve_deposit(
    deposit_id: uuid.UUID,
    request: Request,
    admin: RequireAdmin,
    db: DbSession,
) -> DepositResponse:
    ip, ua = _extract_client_meta(request)
    return await approve_deposit(db, deposit_id, admin.id, ip_address=ip, user_agent=ua)


@router.post(
    "/admin/wallet/deposits/{deposit_id}/reject",
    response_model=DepositResponse,
    summary="Admin rejects deposit with audit log (§7, §10)",
)
async def admin_reject_deposit(
    deposit_id: uuid.UUID,
    payload: RejectDepositRequest,
    request: Request,
    admin: RequireAdmin,
    db: DbSession,
) -> DepositResponse:
    ip, ua = _extract_client_meta(request)
    return await reject_deposit(
        db, deposit_id, admin.id, reason=payload.reason, ip_address=ip, user_agent=ua
    )


@router.post(
    "/admin/wallet/manual-topup",
    response_model=AdminManualTopUpResponse,
    summary="Admin directly credits a student's wallet balance with double-entry transaction",
)
async def admin_topup_user_wallet(
    payload: AdminManualTopUpRequest,
    request: Request,
    admin: RequireAdmin,
    db: DbSession,
) -> AdminManualTopUpResponse:
    ip, ua = _extract_client_meta(request)
    return await admin_manual_user_topup(
        db,
        admin_id=admin.id,
        user_identifier=payload.user_identifier,
        amount_egp=payload.amount_egp,
        note=payload.note,
        ip_address=ip,
        user_agent=ua,
    )
