"""Orders API router (§10, §11, §12)."""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.common.deps import DbSession
from app.modules.identity.deps import RequireUser
from app.modules.orders.schemas import CheckoutRequest, OrderResponse
from app.modules.orders.service import checkout_product, list_user_orders

router = APIRouter(prefix="/orders", tags=["orders"])


@router.post("/checkout", response_model=OrderResponse, status_code=201)
async def checkout(
    request: Request,
    payload: CheckoutRequest,
    current_user: RequireUser,
    db: DbSession,
) -> OrderResponse:
    """Execute wallet checkout for a product and grant server-side entitlement."""
    ip_address = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")
    return await checkout_product(
        db,
        user_id=current_user.id,
        request=payload,
        ip_address=ip_address,
        user_agent=user_agent,
    )


@router.get("", response_model=list[OrderResponse])
async def get_orders(
    current_user: RequireUser,
    db: DbSession,
) -> list[OrderResponse]:
    """List purchase history for current user."""
    return await list_user_orders(db, current_user.id)
