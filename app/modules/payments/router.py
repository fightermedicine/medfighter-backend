"""Payments API router.

User endpoints:
  POST  /payments/initiate          → initiate payment, get reference
  POST  /payments/{id}/proof        → upload proof screenshot (multipart)
  GET   /payments/my                → list my payments
  GET   /payments/{id}              → get single payment status

Admin endpoints:
  GET   /admin/payments/pending     → pending review queue
  GET   /admin/payments/{id}        → payment detail
  GET   /admin/payments/{id}/proof  → serve proof image (protected)
  POST  /admin/payments/{id}/approve
  POST  /admin/payments/{id}/reject
  POST  /admin/payments/{id}/request-proof
"""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Request, UploadFile, status
from fastapi.responses import FileResponse

from app.common.deps import DbSession
from app.modules.identity.deps import RequireAdmin, RequireUser
from app.modules.payments.schemas import (
    AdminPaymentDetail,
    InitiatePaymentRequest,
    PaymentResponse,
    PendingPaymentsResponse,
    RejectPaymentRequest,
)
from app.modules.payments.service import (
    approve_payment,
    get_payment,
    get_payment_as_admin,
    get_user_payments,
    initiate_payment,
    list_pending_payments,
    reject_payment,
    request_new_proof,
    submit_proof,
)

router = APIRouter(tags=["payments"])

_MAX_PROOF_SIZE = 10 * 1024 * 1024  # 10 MB


def _client_meta(request: Request) -> tuple[str | None, str | None]:
    return (
        request.client.host if request.client else None,
        request.headers.get("user-agent"),
    )


# ── User endpoints ─────────────────────────────────────────────────────────────

@router.post(
    "/payments/initiate",
    response_model=PaymentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Initiate a Vodafone Cash payment for a product",
)
async def user_initiate_payment(
    payload: InitiatePaymentRequest,
    current_user: RequireUser,
    request: Request,
    db: DbSession,
) -> PaymentResponse:
    ip, ua = _client_meta(request)
    return await initiate_payment(
        db, current_user.id, payload.product_id, ip_address=ip, user_agent=ua
    )


@router.post(
    "/payments/{payment_id}/proof",
    response_model=PaymentResponse,
    summary="Upload payment proof screenshot",
)
async def user_upload_proof(
    payment_id: uuid.UUID,
    file: UploadFile,
    current_user: RequireUser,
    request: Request,
    db: DbSession,
) -> PaymentResponse:
    content_type = file.content_type or "image/jpeg"
    if not content_type.startswith("image/"):
        from app.core.errors import ProblemError
        raise ProblemError(
            status_code=400,
            code="invalid_file_type",
            detail="Only image files are accepted as proof.",
        )
    file_bytes = await file.read(_MAX_PROOF_SIZE + 1)
    if len(file_bytes) > _MAX_PROOF_SIZE:
        from app.core.errors import ProblemError
        raise ProblemError(
            status_code=413,
            code="file_too_large",
            detail="Proof screenshot must be under 10 MB.",
        )
    ip, ua = _client_meta(request)
    return await submit_proof(
        db, payment_id, current_user.id, file_bytes, content_type,
        ip_address=ip, user_agent=ua,
    )


@router.get(
    "/payments/my",
    response_model=list[PaymentResponse],
    summary="List my payments",
)
async def user_list_my_payments(
    current_user: RequireUser,
    db: DbSession,
) -> list[PaymentResponse]:
    return await get_user_payments(db, current_user.id)


@router.get(
    "/payments/{payment_id}",
    response_model=PaymentResponse,
    summary="Get a single payment status",
)
async def user_get_payment(
    payment_id: uuid.UUID,
    current_user: RequireUser,
    db: DbSession,
) -> PaymentResponse:
    return await get_payment(db, payment_id, current_user.id)


# ── Admin endpoints ─────────────────────────────────────────────────────────────

@router.get(
    "/admin/payments/pending",
    response_model=PendingPaymentsResponse,
    summary="Admin: list all payments under review",
)
async def admin_list_pending(
    _admin: RequireAdmin,
    db: DbSession,
) -> PendingPaymentsResponse:
    return await list_pending_payments(db)


@router.get(
    "/admin/payments/{payment_id}",
    response_model=AdminPaymentDetail,
    summary="Admin: get payment detail",
)
async def admin_get_payment(
    payment_id: uuid.UUID,
    _admin: RequireAdmin,
    db: DbSession,
) -> AdminPaymentDetail:
    return await get_payment_as_admin(db, payment_id)


@router.get(
    "/admin/payments/{payment_id}/proof",
    summary="Admin: serve payment proof image (protected)",
)
async def admin_get_proof(
    payment_id: uuid.UUID,
    _admin: RequireAdmin,
    db: DbSession,
):
    from app.modules.payments.models import Payment
    from sqlalchemy import select
    payment = await db.scalar(select(Payment).where(Payment.id == payment_id))
    if not payment or not payment.proof_path:
        from app.core.errors import NotFound
        raise NotFound("Proof")
    proof_path = Path(payment.proof_path)
    if not proof_path.exists():
        from app.core.errors import NotFound
        raise NotFound("Proof file")
    media_type = payment.proof_content_type or "image/jpeg"
    return FileResponse(path=str(proof_path), media_type=media_type)


@router.post(
    "/admin/payments/{payment_id}/approve",
    response_model=PaymentResponse,
    summary="Admin: atomically approve payment (ledger + entitlement in one transaction)",
)
async def admin_approve(
    payment_id: uuid.UUID,
    admin: RequireAdmin,
    request: Request,
    db: DbSession,
) -> PaymentResponse:
    ip, ua = _client_meta(request)
    return await approve_payment(db, payment_id, admin.id, ip_address=ip, user_agent=ua)


@router.post(
    "/admin/payments/{payment_id}/reject",
    response_model=PaymentResponse,
    summary="Admin: reject payment with mandatory reason",
)
async def admin_reject(
    payment_id: uuid.UUID,
    payload: RejectPaymentRequest,
    admin: RequireAdmin,
    request: Request,
    db: DbSession,
) -> PaymentResponse:
    ip, ua = _client_meta(request)
    return await reject_payment(
        db, payment_id, admin.id, reason=payload.reason, ip_address=ip, user_agent=ua
    )


@router.post(
    "/admin/payments/{payment_id}/request-proof",
    response_model=PaymentResponse,
    summary="Admin: request user to upload new/clearer proof",
)
async def admin_request_proof(
    payment_id: uuid.UUID,
    admin: RequireAdmin,
    request: Request,
    db: DbSession,
) -> PaymentResponse:
    ip, ua = _client_meta(request)
    return await request_new_proof(db, payment_id, admin.id, ip_address=ip, user_agent=ua)
