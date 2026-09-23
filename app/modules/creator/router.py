"""Router for Creator Studio endpoints (§Creator Layer)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.errors import ProblemError
from app.modules.audit.models import AuditLog
from app.modules.catalog.models import Product
from app.modules.identity.deps import RequireCreator
from app.modules.identity.models import User
from app.modules.creator.schemas import (
    CreatorDashboardResponse,
    CreatorStudentLookupRequest,
    CreatorStudentLookupResponse,
    CreatorTopupHistoryItem,
    CreatorTopupRequest,
    CreatorTopupResponse,
)
from app.modules.wallet.service import (
    admin_manual_user_topup,
    calculate_wallet_balance,
    get_or_create_wallet,
)

router = APIRouter(prefix="/v1/creator", tags=["Creator Studio"])


@router.post(
    "/lookup",
    response_model=CreatorStudentLookupResponse,
    status_code=status.HTTP_200_OK,
    summary="Lookup a student by phone, email, or ID before top-up",
)
async def creator_lookup_student(
    body: CreatorStudentLookupRequest,
    creator: RequireCreator,
    db: AsyncSession = Depends(get_db),
) -> CreatorStudentLookupResponse:
    clean_id = body.identifier.strip()
    target_user: User | None = None

    try:
        u_id = uuid.UUID(clean_id)
        target_user = await db.get(User, u_id)
    except ValueError:
        pass

    if not target_user:
        q = select(User).where(
            or_(
                func.lower(User.email) == clean_id.lower(),
                User.phone == clean_id,
            )
        )
        target_user = await db.scalar(q)

    if not target_user:
        raise ProblemError(
            status_code=404,
            code="student_not_found",
            detail=f"No student found with identifier: '{clean_id}'. Ensure the student has registered an account first.",
        )

    wallet = await get_or_create_wallet(db, target_user.id)
    balance_piastres = await calculate_wallet_balance(db, wallet.id)
    current_balance_egp = balance_piastres / 100.0

    return CreatorStudentLookupResponse(
        id=target_user.id,
        full_name=target_user.full_name,
        email=target_user.email,
        phone=target_user.phone,
        medical_year=target_user.medical_year,
        current_balance_egp=current_balance_egp,
        is_active=target_user.is_active,
    )


@router.post(
    "/topup",
    response_model=CreatorTopupResponse,
    status_code=status.HTTP_200_OK,
    summary="Directly credit a student's wallet for course booklet access",
)
async def creator_topup_student(
    body: CreatorTopupRequest,
    creator: RequireCreator,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> CreatorTopupResponse:
    ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent")

    res = await admin_manual_user_topup(
        db,
        admin_id=creator.id,
        user_identifier=body.identifier,
        amount_egp=body.amount_egp,
        note=f"[Creator: {creator.full_name}] {body.note}",
        ip_address=ip,
        user_agent=ua,
    )

    clean_id = body.identifier.strip()
    target_user: User | None = None
    try:
        u_id = uuid.UUID(clean_id)
        target_user = await db.get(User, u_id)
    except ValueError:
        pass
    if not target_user:
        q = select(User).where(
            or_(
                func.lower(User.email) == clean_id.lower(),
                User.phone == clean_id,
            )
        )
        target_user = await db.scalar(q)

    student_name = target_user.full_name if target_user else "Doctor"
    student_phone = target_user.phone if target_user else None
    student_email = target_user.email if target_user else clean_id
    student_id = target_user.id if target_user else creator.id

    student_first = student_name.split()[0] if student_name else "Doctor"
    wa_msg = (
        f"Hello Dr. {student_first},\n"
        f"Your MedFighter wallet has been credited with {body.amount_egp:.2f} EGP successfully.\n"
        f"Current Balance: {res.new_balance_egp:.2f} EGP.\n"
        f"Note: {body.note}\n"
        f"You can now access your medical booklet directly in the MedFighter app."
    )

    return CreatorTopupResponse(
        success=True,
        student_id=student_id,
        student_name=student_name,
        student_email=student_email,
        student_phone=student_phone,
        amount_egp=body.amount_egp,
        new_balance_egp=res.new_balance_egp,
        transaction_id=res.transaction_id,
        timestamp=datetime.now(UTC).isoformat(),
        whatsapp_message=wa_msg,
    )


@router.get(
    "/topup-history",
    response_model=list[CreatorTopupHistoryItem],
    status_code=status.HTTP_200_OK,
    summary="Get recent top-up transactions initiated by this creator",
)
async def creator_topup_history(
    creator: RequireCreator,
    db: AsyncSession = Depends(get_db),
    limit: int = 50,
) -> list[CreatorTopupHistoryItem]:
    stmt = (
        select(AuditLog)
        .where(
            AuditLog.actor_id == creator.id,
            AuditLog.action == "wallet.admin_manual_topup",
        )
        .order_by(desc(AuditLog.created_at))
        .limit(limit)
    )
    logs = (await db.scalars(stmt)).all()

    items: list[CreatorTopupHistoryItem] = []
    for log in logs:
        details = log.details or {}
        tx_id = details.get("transaction_id", str(log.id))
        target_email = details.get("target_email", "Student")
        amount = float(details.get("amount_egp", 0.0))
        note = str(details.get("note", ""))

        items.append(
            CreatorTopupHistoryItem(
                id=str(log.id),
                transaction_id=tx_id,
                student_name=target_email.split("@")[0],
                student_identifier=target_email,
                amount_egp=amount,
                note=note,
                created_at=log.created_at.isoformat(),
            )
        )
    return items


@router.get(
    "/dashboard",
    response_model=CreatorDashboardResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Creator Studio dashboard metrics",
)
async def creator_dashboard(
    creator: RequireCreator,
    db: AsyncSession = Depends(get_db),
) -> CreatorDashboardResponse:
    stmt = (
        select(AuditLog)
        .where(
            AuditLog.actor_id == creator.id,
            AuditLog.action == "wallet.admin_manual_topup",
        )
        .order_by(desc(AuditLog.created_at))
    )
    logs = (await db.scalars(stmt)).all()

    total_amount = 0.0
    students_set: set[str] = set()
    recent_items: list[CreatorTopupHistoryItem] = []

    for log in logs:
        details = log.details or {}
        tx_id = details.get("transaction_id", str(log.id))
        target_email = details.get("target_email", "Student")
        target_id = details.get("target_user_id", "")
        if target_id:
            students_set.add(target_id)
        elif target_email:
            students_set.add(target_email)

        amount = float(details.get("amount_egp", 0.0))
        total_amount += amount
        note = str(details.get("note", ""))

        if len(recent_items) < 15:
            recent_items.append(
                CreatorTopupHistoryItem(
                    id=str(log.id),
                    transaction_id=tx_id,
                    student_name=target_email.split("@")[0],
                    student_identifier=target_email,
                    amount_egp=amount,
                    note=note,
                    created_at=log.created_at.isoformat(),
                )
            )

    # Count published products
    prod_stmt = select(func.count(Product.id)).where(Product.is_active == True)
    total_booklets = await db.scalar(prod_stmt) or 0

    return CreatorDashboardResponse(
        creator_id=creator.id,
        creator_name=creator.full_name,
        creator_email=creator.email,
        total_students_credited=len(students_set),
        total_amount_credited_egp=total_amount,
        total_booklets_published=total_booklets,
        medzone_booklet_price=0.0,
        recent_topups=recent_items,
    )


@router.get(
    "/students",
    status_code=status.HTTP_200_OK,
    summary="Get full roster of students who purchased or were credited by this creator",
)
async def creator_get_students(
    creator: RequireCreator,
    db: AsyncSession = Depends(get_db),
    limit: int = 150,
) -> list[dict]:
    stmt = (
        select(AuditLog)
        .where(
            AuditLog.actor_id == creator.id,
            AuditLog.action == "wallet.admin_manual_topup",
        )
        .order_by(desc(AuditLog.created_at))
        .limit(limit)
    )
    logs = (await db.scalars(stmt)).all()

    student_map: dict[str, dict] = {}
    for log in logs:
        details = log.details or {}
        email = details.get("target_email") or details.get("target_user_id") or "Unknown"
        amount = float(details.get("amount_egp", 0.0))
        note = details.get("note", "Course Booklet")

        if email not in student_map:
            student_map[email] = {
                "identifier": email,
                "email": email,
                "full_name": email.split("@")[0] if "@" in email else "Student",
                "phone": "",
                "medical_year": 4,
                "total_credited_egp": amount,
                "latest_booklet": note,
                "last_active": log.created_at.strftime("%Y-%m-%d %H:%M"),
                "transactions_count": 1,
            }
        else:
            student_map[email]["total_credited_egp"] += amount
            student_map[email]["transactions_count"] += 1

    # Enrich with actual user full names and phones if exists
    if student_map:
        emails = list(student_map.keys())
        users_stmt = select(User).where(or_(User.email.in_(emails), User.phone.in_(emails)))
        users = (await db.scalars(users_stmt)).all()
        for u in users:
            key = u.email if u.email in student_map else (u.phone if u.phone in student_map else None)
            if key and key in student_map:
                student_map[key]["full_name"] = u.full_name
                student_map[key]["phone"] = u.phone or ""
                student_map[key]["medical_year"] = u.medical_year

    return list(student_map.values())


@router.get(
    "/booklets",
    status_code=status.HTTP_200_OK,
    summary="Get list of booklets published by or attributed to this creator",
)
async def creator_get_booklets(
    creator: RequireCreator,
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    stmt = (
        select(Product)
        .where(Product.is_active == True)
        .order_by(desc(Product.created_at))
        .limit(100)
    )
    products = (await db.scalars(stmt)).all()

    items: list[dict] = []
    for p in products:
        items.append({
            "id": str(p.id),
            "title": p.title,
            "author": creator.full_name,
            "medical_year": p.medical_year,
            "folder_id": str(p.folder_id) if p.folder_id else None,
            "price_egp": p.price_piastres / 100.0,
            "status": "ACTIVE" if p.is_active else "INACTIVE",
            "format": "Encrypted PDF (DRM Protected)",
            "description": p.description or "",
            "is_preview_available": bool(p.preview_data),
        })
    return items
