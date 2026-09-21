"""Voucher and Promo HTTP endpoints (§11, §35)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.modules.identity.deps import RequireAdmin, RequireUser
from app.modules.vouchers.schemas import (
    RedeemVoucherRequest,
    RedeemVoucherResponse,
    VoucherBatchCreate,
    VoucherBatchOut,
    VoucherOut,
)
from app.modules.vouchers.service import (
    create_voucher_batch,
    list_vouchers,
    redeem_voucher,
)

router = APIRouter(prefix="", tags=["vouchers"])


@router.post(
    "/v1/vouchers/redeem",
    response_model=RedeemVoucherResponse,
    status_code=status.HTTP_200_OK,
    summary="Student redeems a voucher code for wallet credit or course access",
)
async def user_redeem_voucher(
    payload: RedeemVoucherRequest,
    user: RequireUser,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> RedeemVoucherResponse:
    ip = request.client.host if request.client else None
    ua = request.headers.get("User-Agent")
    return await redeem_voucher(db, user.id, payload.code, ip_address=ip, user_agent=ua)


@router.post(
    "/v1/admin/vouchers/batch",
    response_model=VoucherBatchOut,
    status_code=status.HTTP_201_CREATED,
    summary="Admin generates a batch of unique voucher redemption codes",
)
async def admin_mint_voucher_batch(
    payload: VoucherBatchCreate,
    admin: RequireAdmin,
    db: AsyncSession = Depends(get_db),
) -> VoucherBatchOut:
    batch = await create_voucher_batch(db, payload, admin.id)
    vouchers_out = [
        VoucherOut(
            id=v.id,
            code=v.code,
            voucher_type=v.voucher_type,
            credit_piastres=v.credit_piastres,
            credit_egp=v.credit_piastres / 100.0,
            product_id=v.product_id,
            max_redemptions=v.max_redemptions,
            redemptions_count=v.redemptions_count,
            is_active=v.is_active,
            expires_at=v.expires_at,
            created_at=v.created_at,
        )
        for v in batch.vouchers
    ]
    return VoucherBatchOut(
        id=batch.id,
        name=batch.name,
        batch_code=batch.batch_code,
        vouchers_count=len(batch.vouchers),
        created_at=batch.created_at,
        vouchers=vouchers_out,
    )


@router.get(
    "/v1/admin/vouchers",
    response_model=list[VoucherOut],
    status_code=status.HTTP_200_OK,
    summary="Admin lists all recent voucher redemption codes",
)
async def admin_get_vouchers(
    admin: RequireAdmin,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
) -> list[VoucherOut]:
    return await list_vouchers(db, limit=limit)
