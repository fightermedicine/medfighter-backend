"""Orders domain service (§10, §11, §12, §47, §48).

Enforces:
- Atomic checkout transaction: lock / verify balance → ledger debit →
  order → entitlement → audit.
- Server-authoritative pricing (client cannot set prices).
- Strict idempotency via idempotency_key.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.errors import Conflict, Forbidden, NotFound, ProblemError
from app.core.money import format_egp
from app.modules.audit.service import record_audit_log
from app.modules.catalog.models import Bundle, Product
from app.modules.entitlement.models import Entitlement
from app.modules.orders.models import Order, PurchaseUnit
from app.modules.orders.schemas import CheckoutRequest, OrderResponse, PurchaseUnitResponse
from app.modules.wallet.models import LedgerEntry, LedgerTransaction
from app.modules.wallet.service import (
    calculate_wallet_balance,
    get_or_create_system_revenue_account,
    get_or_create_wallet,
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _to_order_response(order: Order, product_title: str | None = None) -> OrderResponse:
    purchase_units = [
        PurchaseUnitResponse(
            id=pu.id,
            unit_index=pu.unit_index,
            status=pu.status,
            assigned_user_id=pu.assigned_user_id,
            entitlement_id=pu.entitlement_id,
        )
        for pu in getattr(order, "purchase_units", [])
    ]
    return OrderResponse(
        id=order.id,
        user_id=order.user_id,
        product_id=order.product_id,
        product_title=product_title,
        quantity=order.quantity,
        amount_piastres=order.amount_piastres,
        amount_egp=order.amount_piastres / 100.0,
        currency=order.currency,
        transaction_id=order.transaction_id,
        status=order.status,
        created_at=order.created_at,
        purchase_units=purchase_units,
    )


async def checkout_product(
    db: AsyncSession,
    user_id: uuid.UUID,
    request: CheckoutRequest,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> OrderResponse:
    """Execute atomic product purchase with double-entry debit and entitlement grant (§12)."""
    # 1. Fetch and validate product with price rules and bundle items
    query = (
        select(Product)
        .options(
            selectinload(Product.price_rules),
            selectinload(Product.bundle).selectinload(Bundle.items),
        )
        .where(Product.id == request.product_id)
    )
    product = await db.scalar(query)
    if not product:
        raise NotFound("Product")
    if not product.is_active:
        raise Forbidden("Product is not currently available for purchase.")

    quantity = max(1, request.quantity)

    # 2. Check for idempotent retry
    if request.idempotency_key:
        existing_tx = await db.scalar(
            select(LedgerTransaction).where(
                LedgerTransaction.idempotency_key == request.idempotency_key
            )
        )
        if existing_tx:
            existing_order = await db.scalar(
                select(Order)
                .options(selectinload(Order.purchase_units))
                .where(Order.transaction_id == existing_tx.id)
            )
            if existing_order:
                return _to_order_response(existing_order, product.title)

    # 3. Prevent duplicate active single-copy entitlements (if quantity == 1)
    if quantity == 1:
        existing_entitlement = await db.scalar(
            select(Entitlement).where(
                Entitlement.user_id == user_id,
                Entitlement.product_id == product.id,
                Entitlement.status == "ACTIVE",
            )
        )
        if existing_entitlement:
            raise Conflict("You already own an active entitlement for this product.")

    # 4. Quantity-tiered pricing (Photo 5: Buy 3 Copies - Small Discount)
    discount_percent = 0
    if product.price_rules:
        applicable = [r for r in product.price_rules if quantity >= r.min_quantity]
        if applicable:
            discount_percent = max(r.discount_percent for r in applicable)
    elif quantity >= 3:
        # Default standard 10% volume discount for 3+ copies if not configured
        discount_percent = 10

    base_total = product.price_piastres * quantity
    discount_piastres = (base_total * discount_percent) // 100
    total_amount_piastres = base_total - discount_piastres

    # 5. Check wallet balance
    user_wallet = await get_or_create_wallet(db, user_id, currency=product.currency)
    balance_piastres = await calculate_wallet_balance(db, user_wallet.id)

    if balance_piastres < total_amount_piastres:
        raise ProblemError(
            status_code=402,
            code="insufficient_funds",
            detail=(
                f"Insufficient wallet balance. Required: {format_egp(total_amount_piastres)}, "
                f"Available: {format_egp(balance_piastres)}."
            ),
        )

    # 6. Post double-entry ledger transaction: Debit User Wallet, Credit System Revenue
    revenue_account = await get_or_create_system_revenue_account(db, currency=product.currency)
    now = _utc_now()
    tx = LedgerTransaction(
        idempotency_key=request.idempotency_key,
        reference=f"ORD-{uuid.uuid4().hex[:12].upper()}",
        description=f"Purchase ({quantity}x): {product.title}",
        status="POSTED",
        created_at=now,
        posted_at=now,
    )
    db.add(tx)
    await db.flush()

    user_entry = LedgerEntry(
        transaction_id=tx.id,
        account_id=user_wallet.id,
        direction="DEBIT",
        amount_piastres=total_amount_piastres,
        created_at=now,
    )
    revenue_entry = LedgerEntry(
        transaction_id=tx.id,
        account_id=revenue_account.id,
        direction="CREDIT",
        amount_piastres=total_amount_piastres,
        created_at=now,
    )
    db.add_all([user_entry, revenue_entry])

    # 7. Create Order record
    order = Order(
        user_id=user_id,
        product_id=product.id,
        quantity=quantity,
        amount_piastres=total_amount_piastres,
        currency=product.currency,
        transaction_id=tx.id,
        status="COMPLETED",
        created_at=now,
    )
    db.add(order)
    await db.flush()

    # 8. Grant Primary Buyer Entitlement for Unit 1
    buyer_entitlement = await db.scalar(
        select(Entitlement).where(
            Entitlement.user_id == user_id,
            Entitlement.product_id == product.id,
            Entitlement.status == "ACTIVE",
        )
    )
    if not buyer_entitlement:
        buyer_entitlement = Entitlement(
            user_id=user_id,
            product_id=product.id,
            status="ACTIVE",
            granted_at=now,
        )
        db.add(buyer_entitlement)
        await db.flush()

    # 9. Create PurchaseUnits for all copies
    purchase_units: list[PurchaseUnit] = []
    for idx in range(1, quantity + 1):
        if idx == 1:
            pu = PurchaseUnit(
                order_id=order.id,
                product_id=product.id,
                unit_index=1,
                status="ASSIGNED",
                assigned_user_id=user_id,
                entitlement_id=buyer_entitlement.id,
                created_at=now,
            )
        else:
            pu = PurchaseUnit(
                order_id=order.id,
                product_id=product.id,
                unit_index=idx,
                status="UNASSIGNED",
                assigned_user_id=None,
                entitlement_id=None,
                created_at=now,
            )
        db.add(pu)
        purchase_units.append(pu)

    # 10. Grant entitlements for bundled items if product is a Bundle
    if product.bundle and product.bundle.items:
        for bi in product.bundle.items:
            existing_bundle_ent = await db.scalar(
                select(Entitlement).where(
                    Entitlement.user_id == user_id,
                    Entitlement.product_id == bi.item_product_id,
                    Entitlement.status == "ACTIVE",
                )
            )
            if not existing_bundle_ent:
                bundle_ent = Entitlement(
                    user_id=user_id,
                    product_id=bi.item_product_id,
                    status="ACTIVE",
                    granted_at=now,
                )
                db.add(bundle_ent)

    # 11. Record audit log
    await record_audit_log(
        db,
        action="orders.purchase_completed",
        resource_type="order",
        actor_id=user_id,
        resource_id=str(order.id),
        details={
            "product_id": str(product.id),
            "quantity": quantity,
            "discount_percent": discount_percent,
            "amount_piastres": total_amount_piastres,
            "transaction_id": str(tx.id),
            "entitlement_id": str(buyer_entitlement.id),
        },
        ip_address=ip_address,
        user_agent=user_agent,
    )

    await db.commit()
    await db.refresh(order)

    return OrderResponse(
        id=order.id,
        user_id=order.user_id,
        product_id=order.product_id,
        product_title=product.title,
        quantity=order.quantity,
        amount_piastres=order.amount_piastres,
        amount_egp=order.amount_piastres / 100.0,
        currency=order.currency,
        transaction_id=order.transaction_id,
        status=order.status,
        created_at=order.created_at,
        purchase_units=[
            PurchaseUnitResponse(
                id=pu.id,
                unit_index=pu.unit_index,
                status=pu.status,
                assigned_user_id=pu.assigned_user_id,
                entitlement_id=pu.entitlement_id,
            )
            for pu in purchase_units
        ],
    )


async def list_user_orders(
    db: AsyncSession,
    user_id: uuid.UUID,
) -> list[OrderResponse]:
    """Retrieve purchase history for a user."""
    query = (
        select(Order, Product.title)
        .join(Product, Order.product_id == Product.id)
        .options(selectinload(Order.purchase_units))
        .where(Order.user_id == user_id)
        .order_by(Order.created_at.desc())
    )
    rows = (await db.execute(query)).all()
    return [_to_order_response(order, title) for order, title in rows]
