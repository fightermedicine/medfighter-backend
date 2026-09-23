"""Admin HTTP router (§38–§40)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.modules.admin.schemas import (
    AdminAssignCreatorFolderRequest,
    AdminCourseCreateRequest,
    AdminCourseOut,
    AdminCurriculumCreateRequest,
    AdminCurriculumItemOut,
    AdminDeckCreateRequest,
    AdminDeckOut,
    AdminDeckUpdateRequest,
    AdminDemoteRequest,
    AdminDeviceOut,
    AdminPdfUploadOut,
    AdminPromoteRequest,
    AdminQuizCreateRequest,
    AdminQuizOut,
    AdminQuizResultsResponse,
    AdminQuizUpdateRequest,
    AdminStatsOut,
    AdminTeamMemberOut,
    AdminCourseUpdateRequest,
    AdminTopUpRequest,
    AdminTopUpResponse,
    AdminTopDownRequest,
    AdminTopDownResponse,
    AdminUserEntitlementOut,
    AdminUserOut,
    AdminUserStatusRequest,
    AdminGrantEntitlementRequest,
    ContactInfoOut,
    ContactInfoUpdate,
    EmergencyLockRequest,
    EmergencyLockResponse,
    SecuritySettingsOut,
    SecuritySettingsUpdate,
)
from app.modules.admin.service import (
    add_course_curriculum_item,
    admin_topup_user,
    admin_deduct_user_balance,
    delete_user,
    create_flashcard_deck,
    create_mcq_quiz,
    create_published_course,
    delete_course,
    delete_course_curriculum_item,
    delete_deck,
    delete_quiz,
    demote_admin_user,
    emergency_lockdown,
    get_admin_stats,
    get_contact_info,
    get_quiz_admin_results,
    get_security_settings,
    grant_user_entitlement_admin,
    list_admin_courses,
    list_admin_decks,
    list_admin_quizzes,
    list_admin_team,
    list_course_curriculum_items,
    list_registered_devices,
    list_user_entitlements_admin,
    promote_user_to_admin,
    revoke_all_user_devices,
    revoke_device_binding,
    revoke_user_entitlement_admin,
    search_students,
    set_user_status,
    update_contact_info,
    update_flashcard_deck,
    update_mcq_quiz,
    update_published_course,
    update_security_settings,
    upload_pdf_document,
)
from app.modules.identity.deps import RequireAdmin, RequireCreator, RequireSuperAdmin

router = APIRouter(prefix="", tags=["admin"])


@router.get(
    "/v1/admin/stats",
    response_model=AdminStatsOut,
    status_code=status.HTTP_200_OK,
    summary="Get platform KPI analytics (Admin only)",
)
async def admin_stats(
    admin: RequireAdmin,
    db: AsyncSession = Depends(get_db),
) -> AdminStatsOut:
    data = await get_admin_stats(db)
    return AdminStatsOut(**data)


@router.get(
    "/v1/admin/users",
    response_model=list[AdminUserOut],
    status_code=status.HTTP_200_OK,
    summary="Search and list registered doctors/students with full profile & control rosters (Admin only)",
)
async def admin_search_students(
    admin: RequireAdmin,
    q: str | None = None,
    medical_year: int | None = None,
    is_active: bool | None = None,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
) -> list[AdminUserOut]:
    data = await search_students(
        db, query=q, medical_year=medical_year, is_active=is_active, limit=limit
    )
    return [AdminUserOut(**u) for u in data]


@router.post(
    "/v1/admin/users/{user_id}/status",
    status_code=status.HTTP_200_OK,
    summary="Ban/suspend or reactivate user account (Admin only)",
)
async def admin_update_user_status(
    user_id: uuid.UUID,
    payload: AdminUserStatusRequest,
    admin: RequireAdmin,
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await set_user_status(
        db,
        admin_id=admin.id,
        user_id=user_id,
        is_active=payload.is_active,
        reason=payload.reason,
    )


@router.post(
    "/v1/admin/users/{user_id}/devices/revoke-all",
    status_code=status.HTTP_200_OK,
    summary="Revoke all hardware device bindings for user (Admin only)",
)
async def admin_revoke_all_devices(
    user_id: uuid.UUID,
    admin: RequireAdmin,
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await revoke_all_user_devices(
        db,
        admin_id=admin.id,
        user_id=user_id,
    )


@router.get(
    "/v1/admin/users/{user_id}/entitlements",
    response_model=list[AdminUserEntitlementOut],
    status_code=status.HTTP_200_OK,
    summary="List course entitlements owned by student (Admin only)",
)
async def admin_get_user_entitlements(
    user_id: uuid.UUID,
    admin: RequireAdmin,
    db: AsyncSession = Depends(get_db),
) -> list[AdminUserEntitlementOut]:
    items = await list_user_entitlements_admin(db, user_id=user_id)
    return [AdminUserEntitlementOut(**i) for i in items]


@router.post(
    "/v1/admin/users/{user_id}/entitlements",
    response_model=AdminUserEntitlementOut,
    status_code=status.HTTP_200_OK,
    summary="Manually grant course/product entitlement to student (Admin only)",
)
async def admin_grant_entitlement(
    user_id: uuid.UUID,
    payload: AdminGrantEntitlementRequest,
    admin: RequireAdmin,
    db: AsyncSession = Depends(get_db),
) -> AdminUserEntitlementOut:
    res = await grant_user_entitlement_admin(
        db,
        admin_id=admin.id,
        user_id=user_id,
        product_id=payload.product_id,
        reason=payload.reason,
        expires_at=payload.expires_at,
    )
    return AdminUserEntitlementOut(**res)


@router.delete(
    "/v1/admin/users/{user_id}/entitlements/{entitlement_id}",
    status_code=status.HTTP_200_OK,
    summary="Revoke course/product entitlement from student (Admin only)",
)
async def admin_revoke_entitlement(
    user_id: uuid.UUID,
    entitlement_id: uuid.UUID,
    admin: RequireAdmin,
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await revoke_user_entitlement_admin(
        db,
        admin_id=admin.id,
        user_id=user_id,
        entitlement_id=entitlement_id,
    )


@router.post(
    "/v1/admin/users/{user_id}/topup",
    response_model=AdminTopUpResponse,
    status_code=status.HTTP_200_OK,
    summary="Directly top up student wallet balance with double-entry journal (Admin only)",
)
async def admin_topup(
    user_id: uuid.UUID,
    payload: AdminTopUpRequest,
    admin: RequireAdmin,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> AdminTopUpResponse:
    ip = request.client.host if request.client else None
    ua = request.headers.get("User-Agent")
    res = await admin_topup_user(
        db,
        admin_id=admin.id,
        user_id=user_id,
        amount_egp=payload.amount_egp,
        reason=payload.reason,
        ip_address=ip,
        user_agent=ua,
    )
    return AdminTopUpResponse(**res)


@router.post(
    "/v1/admin/users/{user_id}/topdown",
    response_model=AdminTopDownResponse,
    status_code=status.HTTP_200_OK,
    summary="Directly deduct student wallet balance with double-entry journal (Admin only)",
)
async def admin_topdown(
    user_id: uuid.UUID,
    payload: AdminTopDownRequest,
    admin: RequireAdmin,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> AdminTopDownResponse:
    ip = request.client.host if request.client else None
    ua = request.headers.get("User-Agent")
    res = await admin_deduct_user_balance(
        db,
        admin_id=admin.id,
        user_id=user_id,
        amount_egp=payload.amount_egp,
        reason=payload.reason,
        ip_address=ip,
        user_agent=ua,
    )
    return AdminTopDownResponse(**res)


@router.delete(
    "/v1/admin/users/{user_id}",
    status_code=status.HTTP_200_OK,
    summary="Permanently delete a registered student/user (Admin only)",
)
async def admin_delete_user(
    user_id: uuid.UUID,
    admin: RequireAdmin,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    ip = request.client.host if request.client else None
    ua = request.headers.get("User-Agent")
    return await delete_user(
        db,
        admin_id=admin.id,
        user_id=user_id,
        ip_address=ip,
        user_agent=ua,
    )


@router.get(
    "/v1/admin/devices",
    response_model=list[AdminDeviceOut],
    status_code=status.HTTP_200_OK,
    summary="List registered student hardware devices (Admin only)",
)
async def admin_devices(
    admin: RequireAdmin,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
) -> list[AdminDeviceOut]:
    data = await list_registered_devices(db, limit=limit)
    return [AdminDeviceOut(**d) for d in data]


@router.post(
    "/v1/admin/devices/{device_id}/revoke",
    status_code=status.HTTP_200_OK,
    summary="Unbind/revoke student hardware device (Admin only)",
)
async def admin_revoke_device(
    device_id: uuid.UUID,
    admin: RequireAdmin,
    db: AsyncSession = Depends(get_db),
) -> dict:
    await revoke_device_binding(db, admin_id=admin.id, device_id=device_id)
    return {"success": True, "message": f"Device {device_id} successfully revoked."}


@router.post(
    "/v1/admin/emergency-lock",
    response_model=EmergencyLockResponse,
    status_code=status.HTTP_200_OK,
    summary="Emergency kill switch: freezes leaker account & wipes sessions (Admin only)",
)
async def admin_emergency_lock(
    payload: EmergencyLockRequest,
    admin: RequireAdmin,
    db: AsyncSession = Depends(get_db),
) -> EmergencyLockResponse:
    res = await emergency_lockdown(
        db,
        admin_id=admin.id,
        identifier=payload.identifier,
        reason=payload.reason,
    )
    return EmergencyLockResponse(**res)


# ==============================================================================
# Creator & Curriculum Endpoints (Admin-Only §35, §37)
# ==============================================================================


@router.post(
    "/v1/admin/courses",
    response_model=AdminCourseOut,
    status_code=status.HTTP_201_CREATED,
    summary="Publish a new course with price & discount rules (Creator/Admin only)",
)
async def admin_create_course(
    payload: AdminCourseCreateRequest,
    admin: RequireAdmin,
    db: AsyncSession = Depends(get_db),
) -> AdminCourseOut:
    course = await create_published_course(
        db,
        admin_id=admin.id,
        title=payload.title,
        description=payload.description,
        price_egp=payload.price_egp,
        category=payload.category,
        product_type=payload.product_type,
        medical_year=payload.medical_year,
        folder_id=payload.folder_id,
        preview_data=payload.preview_data,
        discount_percent=payload.discount_percent,
        min_discount_quantity=payload.min_discount_quantity,
    )
    return AdminCourseOut(**course)


@router.get(
    "/v1/admin/courses",
    response_model=list[AdminCourseOut],
    status_code=status.HTTP_200_OK,
    summary="List all published courses for creator management (Creator/Admin only)",
)
async def admin_list_courses(
    admin: RequireAdmin,
    db: AsyncSession = Depends(get_db),
) -> list[AdminCourseOut]:
    courses = await list_admin_courses(db)
    return [AdminCourseOut(**c) for c in courses]


@router.get(
    "/v1/admin/courses/{course_id}/content",
    response_model=list[AdminCurriculumItemOut],
    status_code=status.HTTP_200_OK,
    summary="List all curriculum items (videos, PDFs, links) for a course (Creator/Admin only)",
)
async def admin_get_course_curriculum_items(
    course_id: uuid.UUID,
    admin: RequireAdmin,
    db: AsyncSession = Depends(get_db),
) -> list[AdminCurriculumItemOut]:
    items = await list_course_curriculum_items(db, course_id=course_id)
    return [AdminCurriculumItemOut(**item) for item in items]


@router.post(
    "/v1/admin/courses/{course_id}/content",
    status_code=status.HTTP_201_CREATED,
    summary="Attach a video stream, external link, or PDF to a course (Creator/Admin only)",
)
async def admin_add_curriculum_item(
    course_id: uuid.UUID,
    payload: AdminCurriculumCreateRequest,
    admin: RequireAdmin,
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await add_course_curriculum_item(
        db,
        admin_id=admin.id,
        course_id=course_id,
        title=payload.title,
        content_type=payload.content_type,
        url_or_path=payload.url_or_path,
        duration_seconds=payload.duration_seconds,
    )


@router.delete(
    "/v1/admin/courses/{course_id}/content/{item_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete a curriculum item from a course (Creator/Admin only)",
)
async def admin_delete_course_curriculum_item(
    course_id: uuid.UUID,
    item_id: uuid.UUID,
    admin: RequireAdmin,
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await delete_course_curriculum_item(
        db, admin_id=admin.id, course_id=course_id, item_id=item_id
    )


@router.post(
    "/v1/admin/quizzes",
    response_model=AdminQuizOut,
    status_code=status.HTTP_201_CREATED,
    summary="Author and publish a medical MCQ quiz bank (Creator/Admin only)",
)
async def admin_create_quiz(
    payload: AdminQuizCreateRequest,
    admin: RequireAdmin,
    db: AsyncSession = Depends(get_db),
) -> AdminQuizOut:
    quiz = await create_mcq_quiz(
        db,
        admin_id=admin.id,
        title=payload.title,
        description=payload.description,
        category=payload.category,
        medical_year=payload.medical_year,
        folder_id=payload.folder_id,
        pass_percentage=payload.pass_percentage,
        time_limit_seconds=payload.time_limit_seconds,
        exam_mode=payload.exam_mode,
        show_explanations=payload.show_explanations,
        questions_data=payload.questions,
    )
    return AdminQuizOut(**quiz)


@router.get(
    "/v1/admin/quizzes",
    response_model=list[AdminQuizOut],
    status_code=status.HTTP_200_OK,
    summary="List all authored MCQ quizzes (Creator/Admin only)",
)
async def admin_list_quizzes(
    admin: RequireAdmin,
    db: AsyncSession = Depends(get_db),
) -> list[AdminQuizOut]:
    quizzes = await list_admin_quizzes(db)
    return [AdminQuizOut(**q) for q in quizzes]


@router.get(
    "/v1/admin/quizzes/{quiz_id}/results",
    response_model=AdminQuizResultsResponse,
    status_code=status.HTTP_200_OK,
    summary="Fetch student performance ledger, rankings, and telemetry for a quiz (Admin only)",
)
async def admin_get_quiz_results(
    quiz_id: uuid.UUID,
    admin: RequireAdmin,
    db: AsyncSession = Depends(get_db),
) -> AdminQuizResultsResponse:
    results = await get_quiz_admin_results(db, quiz_id=quiz_id)
    return AdminQuizResultsResponse(**results)


@router.get(
    "/v1/admin/quizzes/{quiz_id}/results/export",
    status_code=status.HTTP_200_OK,
    summary="Export student quiz ledger as CSV (Admin only)",
)
async def admin_export_quiz_results_csv(
    quiz_id: uuid.UUID,
    admin: RequireAdmin,
    db: AsyncSession = Depends(get_db),
) -> Response:
    import csv
    import io

    data = await get_quiz_admin_results(db, quiz_id=quiz_id)
    output = io.StringIO()
    writer = csv.writer(output)

    # Header
    writer.writerow([
        "Student Name",
        "Email",
        "Phone",
        "Medical Year",
        "Score",
        "Max Score",
        "Percentage",
        "Status",
        "Time Spent (seconds)",
        "Submitted At",
    ])

    for a in data["attempts"]:
        writer.writerow([
            a["student_name"],
            a["student_email"],
            a["student_phone"] or "",
            a["medical_year"],
            a["score"],
            a["max_score"],
            f"{a['percentage']}%",
            "PASSED" if a["passed"] else "FAILED",
            a["time_spent_seconds"],
            str(a["completed_at"]),
        ])

    csv_bytes = output.getvalue().encode("utf-8-sig")
    import urllib.parse

    ascii_filename = f"quiz_results_{str(quiz_id)[:8]}.csv"
    encoded_filename = urllib.parse.quote(f"results_{data['quiz_title']}_{str(quiz_id)[:8]}.csv")

    return Response(
        content=csv_bytes,
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{ascii_filename}"; filename*=UTF-8\'\'{encoded_filename}'
        },
    )


@router.post(
    "/v1/admin/decks",
    response_model=AdminDeckOut,
    status_code=status.HTTP_201_CREATED,
    summary="Author and publish a medical flashcard deck (Creator/Admin only)",
)
async def admin_create_deck(
    payload: AdminDeckCreateRequest,
    admin: RequireAdmin,
    db: AsyncSession = Depends(get_db),
) -> AdminDeckOut:
    deck = await create_flashcard_deck(
        db,
        admin_id=admin.id,
        title=payload.title,
        description=payload.description,
        category=payload.category,
        medical_year=payload.medical_year,
        folder_id=payload.folder_id,
        cards_data=payload.cards,
    )
    return AdminDeckOut(**deck)


@router.get(
    "/v1/admin/decks",
    response_model=list[AdminDeckOut],
    status_code=status.HTTP_200_OK,
    summary="List all authored flashcard decks (Creator/Admin only)",
)
async def admin_list_decks(
    admin: RequireAdmin,
    db: AsyncSession = Depends(get_db),
) -> list[AdminDeckOut]:
    decks = await list_admin_decks(db)
    return [AdminDeckOut(**d) for d in decks]


@router.post(
    "/v1/admin/decks/import-file",
    response_model=AdminDeckOut,
    status_code=status.HTTP_201_CREATED,
    summary="Import an Anki deck (.apkg, .colpkg, .tsv, .csv) and publish as admin deck",
)
async def admin_import_deck_file(
    admin: RequireAdmin,
    db: AsyncSession = Depends(get_db),
    file: UploadFile = File(..., description="Anki package or export file"),
    medical_year: int = Form(1, ge=1, le=6),
    folder_id: str | None = Form(None),
    deck_title: str | None = Form(None),
    category: str = Form("Medical"),
) -> AdminDeckOut:
    from app.modules.learning.service import import_anki_file
    parsed_folder_id = None
    if folder_id and folder_id.strip():
        import uuid as _uuid
        try:
            parsed_folder_id = _uuid.UUID(folder_id.strip())
        except ValueError:
            parsed_folder_id = None

    file_bytes = await file.read()
    deck_resp = await import_anki_file(
        db=db,
        user_id=admin.id,
        file_bytes=file_bytes,
        filename=file.filename or "deck.apkg",
        medical_year=medical_year,
        folder_id=parsed_folder_id,
        deck_title=deck_title,
        category=category,
    )
    return AdminDeckOut(
        id=deck_resp.id,
        title=deck_resp.title,
        description=deck_resp.description or "",
        category=deck_resp.category or category,
        medical_year=deck_resp.medical_year or medical_year,
        folder_id=deck_resp.folder_id,
        total_cards=deck_resp.total_cards,
        is_active=True,
        cards_count=deck_resp.total_cards,
        created_at=deck_resp.created_at,
    )


# ==============================================================================
# PDF Upload Endpoint (Real Server-Side Storage)
# ==============================================================================


@router.post(
    "/v1/admin/courses/upload-pdf",
    response_model=AdminPdfUploadOut,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a PDF document to server storage and publish to curriculum (Creator/Admin only)",
)
async def admin_upload_pdf(
    creator: RequireCreator,
    db: AsyncSession = Depends(get_db),
    file: UploadFile = File(..., description="PDF file to upload"),
    title: str = Form(..., min_length=2, max_length=255),
    description: str = Form("", max_length=1000),
    price_egp: float = Form(0.0, ge=0.0),
    category: str = Form("Medical", max_length=64),
    medical_year: int = Form(1, ge=1, le=6),
    folder_id: str | None = Form(None),
    thumbnail_b64: str | None = Form(None),
) -> AdminPdfUploadOut:
    file_bytes = await file.read()
    parsed_folder_id = None
    if folder_id and folder_id.strip():
        import uuid as _uuid
        try:
            parsed_folder_id = _uuid.UUID(folder_id.strip())
        except ValueError:
            parsed_folder_id = None

    # Server-side enforcement: Creators can ONLY publish into their admin-assigned path
    is_admin = any(ur.role_id in ("ADMIN", "SUPER_ADMIN") for ur in creator.roles)
    if not is_admin:
        if not creator.assigned_folder_id:
            raise ProblemError(
                status_code=403,
                code="publishing_path_required",
                detail="You have not been assigned a publishing curriculum folder by an administrator. Please contact an admin to assign your module/folder.",
            )
        from app.modules.curriculum.models import CurriculumFolder
        is_allowed = False
        if parsed_folder_id == creator.assigned_folder_id:
            is_allowed = True
        elif parsed_folder_id is not None:
            # Check hierarchy if parsed_folder_id is a descendant of assigned_folder_id
            curr = await db.get(CurriculumFolder, parsed_folder_id)
            while curr and curr.parent_id:
                if curr.parent_id == creator.assigned_folder_id:
                    is_allowed = True
                    break
                curr = await db.get(CurriculumFolder, curr.parent_id)
        if not is_allowed:
            raise ProblemError(
                status_code=403,
                code="unauthorized_folder_path",
                detail="Creators can only publish content into their administrator-assigned curriculum module/folder.",
            )

    result = await upload_pdf_document(
        db,
        admin_id=creator.id,
        file_bytes=file_bytes,
        original_filename=file.filename or "document.pdf",
        title=title,
        description=description,
        price_egp=price_egp,
        category=category,
        medical_year=medical_year,
        folder_id=parsed_folder_id,
        preview_data=thumbnail_b64,
    )
    return AdminPdfUploadOut(**result)


# ==============================================================================
# Delete Endpoints
# ==============================================================================


@router.patch(
    "/v1/admin/courses/{course_id}",
    status_code=status.HTTP_200_OK,
    summary="Update a published course metadata, price, year, or active status (Creator/Admin only)",
)
async def admin_update_course(
    course_id: uuid.UUID,
    payload: AdminCourseUpdateRequest,
    admin: RequireAdmin,
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await update_published_course(
        db, admin_id=admin.id, course_id=course_id, payload=payload
    )


@router.patch(
    "/v1/admin/quizzes/{quiz_id}",
    status_code=status.HTTP_200_OK,
    summary="Update a MCQ quiz bank metadata, year, folder, or active status (Creator/Admin only)",
)
async def admin_update_quiz(
    quiz_id: uuid.UUID,
    payload: AdminQuizUpdateRequest,
    admin: RequireAdmin,
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await update_mcq_quiz(
        db, admin_id=admin.id, quiz_id=quiz_id, payload=payload
    )


@router.patch(
    "/v1/admin/decks/{deck_id}",
    status_code=status.HTTP_200_OK,
    summary="Update a flashcard deck metadata, year, folder, or public status (Creator/Admin only)",
)
async def admin_update_deck(
    deck_id: uuid.UUID,
    payload: AdminDeckUpdateRequest,
    admin: RequireAdmin,
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await update_flashcard_deck(
        db, admin_id=admin.id, deck_id=deck_id, payload=payload
    )


@router.delete(
    "/v1/admin/courses/{course_id}",
    status_code=status.HTTP_200_OK,
    summary="Deactivate/delete a published course (Creator/Admin only)",
)
async def admin_delete_course(
    course_id: uuid.UUID,
    admin: RequireAdmin,
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await delete_course(db, admin_id=admin.id, course_id=course_id)


@router.delete(
    "/v1/admin/quizzes/{quiz_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete a MCQ quiz bank and all questions (Creator/Admin only)",
)
async def admin_delete_quiz(
    quiz_id: uuid.UUID,
    admin: RequireAdmin,
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await delete_quiz(db, admin_id=admin.id, quiz_id=quiz_id)


@router.delete(
    "/v1/admin/decks/{deck_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete a flashcard deck and all cards (Creator/Admin only)",
)
async def admin_delete_deck(
    deck_id: uuid.UUID,
    admin: RequireAdmin,
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await delete_deck(db, admin_id=admin.id, deck_id=deck_id)


# ==============================================================================
# Admin Team Management (Super Admin only)
# ==============================================================================


@router.get(
    "/v1/admin/team",
    response_model=list[AdminTeamMemberOut],
    status_code=status.HTTP_200_OK,
    summary="List all administrators on the platform (Admin only)",
)
async def admin_list_team(
    admin: RequireAdmin,
    db: AsyncSession = Depends(get_db),
) -> list[AdminTeamMemberOut]:
    members = await list_admin_team(db)
    return [AdminTeamMemberOut(**m) for m in members]


@router.post(
    "/v1/admin/team/promote",
    status_code=status.HTTP_200_OK,
    summary="Promote a user to Admin or Super Admin (Super Admin only)",
)
async def admin_promote_user(
    body: AdminPromoteRequest,
    super_admin: RequireSuperAdmin,
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await promote_user_to_admin(
        db,
        actor_admin_id=super_admin.id,
        identifier=body.identifier,
        role=body.role,
    )


@router.post(
    "/v1/admin/team/demote",
    status_code=status.HTTP_200_OK,
    summary="Revoke admin privileges from a user (Super Admin only)",
)
async def admin_demote_user(
    body: AdminDemoteRequest,
    super_admin: RequireSuperAdmin,
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await demote_admin_user(
        db,
        actor_admin_id=super_admin.id,
        target_user_id=body.user_id,
    )


# ==============================================================================
# Platform Contact & Payment Info Endpoints
# ==============================================================================


@router.get(
    "/v1/app/contact-info",
    response_model=ContactInfoOut,
    status_code=status.HTTP_200_OK,
    summary="Get current platform payment and support contact information (Public / Student)",
)
async def app_get_contact_info(
    db: AsyncSession = Depends(get_db),
) -> ContactInfoOut:
    data = await get_contact_info(db)
    return ContactInfoOut(**data)


@router.get(
    "/v1/admin/contact-info",
    response_model=ContactInfoOut,
    status_code=status.HTTP_200_OK,
    summary="Get platform payment and support contact information (Admin only)",
)
async def admin_get_contact_info(
    admin: RequireAdmin,
    db: AsyncSession = Depends(get_db),
) -> ContactInfoOut:
    data = await get_contact_info(db)
    return ContactInfoOut(**data)


@router.put(
    "/v1/admin/contact-info",
    response_model=ContactInfoOut,
    status_code=status.HTTP_200_OK,
    summary="Update platform payment and support contact information (Admin only)",
)
async def admin_update_contact_info_endpoint(
    payload: ContactInfoUpdate,
    admin: RequireAdmin,
    db: AsyncSession = Depends(get_db),
) -> ContactInfoOut:
    data = await update_contact_info(db, admin_id=admin.id, payload=payload)
    return ContactInfoOut(**data)


@router.get(
    "/v1/app/security-settings",
    response_model=SecuritySettingsOut,
    status_code=status.HTTP_200_OK,
    summary="Get current platform security policies including screenshot control (Public / Student)",
)
async def app_get_security_settings(
    db: AsyncSession = Depends(get_db),
) -> SecuritySettingsOut:
    data = await get_security_settings(db)
    return SecuritySettingsOut(**data)


@router.get(
    "/v1/admin/security-settings",
    response_model=SecuritySettingsOut,
    status_code=status.HTTP_200_OK,
    summary="Get platform security policies (Admin only)",
)
async def admin_get_security_settings(
    admin: RequireAdmin,
    db: AsyncSession = Depends(get_db),
) -> SecuritySettingsOut:
    data = await get_security_settings(db)
    return SecuritySettingsOut(**data)


@router.put(
    "/v1/admin/security-settings",
    response_model=SecuritySettingsOut,
    status_code=status.HTTP_200_OK,
    summary="Update platform security policies including screenshot control (Admin only)",
)
async def admin_update_security_settings_endpoint(
    payload: SecuritySettingsUpdate,
    admin: RequireAdmin,
    db: AsyncSession = Depends(get_db),
) -> SecuritySettingsOut:
    data = await update_security_settings(db, admin_id=admin.id, payload=payload)
    return SecuritySettingsOut(**data)


# ==============================================================================
# Creator Governance & Audit Ledger Endpoints
# ==============================================================================


@router.get(
    "/v1/admin/creators",
    status_code=status.HTTP_200_OK,
    summary="Get list of all creators and their activity stats with financial revenue split (Admin only)",
)
async def admin_get_creators(
    admin: RequireAdmin,
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    from sqlalchemy import select
    from app.modules.identity.models import User, UserRole
    from app.modules.audit.models import AuditLog

    q = (
        select(User)
        .join(UserRole, UserRole.user_id == User.id)
        .where(UserRole.role_id == "CREATOR")
        .distinct()
    )
    creators = (await db.scalars(q)).all()
    results = []

    for c in creators:
        # Aggregate financial metrics from immutable audit logs
        log_stmt = (
            select(AuditLog)
            .where(
                AuditLog.actor_id == c.id,
                AuditLog.action == "wallet.admin_manual_topup",
            )
        )
        logs = (await db.scalars(log_stmt)).all()

        total_sales = 0.0
        students_set = set()
        for log in logs:
            det = log.details or {}
            amt = float(det.get("amount_egp", 0.0))
            total_sales += amt
            target = det.get("target_user_id") or det.get("target_email")
            if target:
                students_set.add(target)

        # Revenue split: 20% platform cut, 80% creator cut
        platform_cut = round(total_sales * 0.20, 2)
        creator_cut = round(total_sales * 0.80, 2)

        assigned_fid = str(c.assigned_folder_id) if getattr(c, "assigned_folder_id", None) else None
        assigned_name = None
        assigned_year = None
        if assigned_fid:
            from app.modules.curriculum.models import CurriculumFolder
            cf = await db.get(CurriculumFolder, c.assigned_folder_id)
            if cf:
                assigned_name = cf.name
                assigned_year = cf.medical_year

        results.append({
            "id": str(c.id),
            "email": c.email,
            "full_name": c.full_name,
            "phone": c.phone,
            "medical_year": c.medical_year,
            "is_active": c.is_active,
            "created_at": c.created_at.isoformat(),
            "total_students": len(students_set),
            "total_transactions": len(logs),
            "total_sales_egp": round(total_sales, 2),
            "platform_share_egp": platform_cut,
            "creator_share_egp": creator_cut,
            "platform_rate_percent": 20,
            "assigned_folder_id": assigned_fid,
            "assigned_folder_name": assigned_name,
            "assigned_medical_year": assigned_year,
        })
    return results


@router.post(
    "/v1/admin/creators/{creator_id}/assign-folder",
    status_code=status.HTTP_200_OK,
    summary="Assign or reassign designated publishing curriculum folder to creator (Admin only)",
)
async def admin_assign_creator_folder(
    creator_id: uuid.UUID,
    body: AdminAssignCreatorFolderRequest,
    admin: RequireAdmin,
    db: AsyncSession = Depends(get_db),
) -> dict:
    from app.modules.curriculum.models import CurriculumFolder
    from app.modules.identity.models import User
    from app.modules.audit.models import AuditLog

    user = await db.get(User, creator_id)
    if not user:
        raise ProblemError(status_code=404, code="user_not_found", detail="User not found")

    folder_name = None
    medical_year = None
    if body.folder_id and body.folder_id.strip():
        try:
            f_uuid = uuid.UUID(body.folder_id.strip())
        except ValueError:
            raise ProblemError(status_code=400, code="invalid_folder_id", detail="Invalid folder UUID")
        cf = await db.get(CurriculumFolder, f_uuid)
        if not cf:
            raise ProblemError(status_code=404, code="folder_not_found", detail="Curriculum folder not found")
        user.assigned_folder_id = cf.id
        folder_name = cf.name
        medical_year = cf.medical_year
    else:
        user.assigned_folder_id = None

    await record_audit_log(
        db,
        action="ASSIGN_CREATOR_FOLDER",
        resource_type="User",
        resource_id=str(creator_id),
        actor_id=admin.id,
        actor_role="ADMIN",
        details={
            "creator_id": str(creator_id),
            "folder_id": str(user.assigned_folder_id) if user.assigned_folder_id else None,
            "folder_name": folder_name,
        },
    )
    await db.commit()
    return {
        "success": True,
        "creator_id": str(creator_id),
        "assigned_folder_id": str(user.assigned_folder_id) if user.assigned_folder_id else None,
        "assigned_folder_name": folder_name,
        "assigned_medical_year": medical_year,
    }


@router.post(
    "/v1/admin/creators/{creator_id}/demote",
    status_code=status.HTTP_200_OK,
    summary="Revoke creator role from user (Admin only)",
)
async def admin_demote_creator(
    creator_id: uuid.UUID,
    admin: RequireAdmin,
    db: AsyncSession = Depends(get_db),
) -> dict:
    from app.modules.identity.models import UserRole
    from app.modules.audit.models import AuditLog

    q = select(UserRole).where(
        UserRole.user_id == creator_id,
        UserRole.role_id == "CREATOR",
    )
    ur = await db.scalar(q)
    if not ur:
        raise ProblemError(status_code=404, code="creator_not_found", detail="User does not have CREATOR role")

    await db.delete(ur)
    await record_audit_log(
        db,
        action="REVOKE_CREATOR_ROLE",
        resource_type="User",
        resource_id=str(creator_id),
        actor_id=admin.id,
        actor_role="ADMIN",
        details={"revoked_user_id": str(creator_id)},
    )
    await db.commit()
    return {"success": True, "message": "Successfully revoked creator privileges"}


@router.get(
    "/v1/admin/creators/ledger",
    status_code=status.HTTP_200_OK,
    summary="Audit trail of all creator top-up transactions (Admin only)",
)
async def admin_get_creator_ledger(
    admin: RequireAdmin,
    db: AsyncSession = Depends(get_db),
    limit: int = 150,
) -> list[dict]:
    from sqlalchemy import desc, select
    from app.modules.audit.models import AuditLog
    from app.modules.identity.models import User

    stmt = (
        select(AuditLog)
        .where(AuditLog.action == "wallet.admin_manual_topup")
        .order_by(desc(AuditLog.created_at))
        .limit(limit)
    )
    logs = (await db.scalars(stmt)).all()

    # Pre-fetch creator names
    creator_ids = {log.actor_id for log in logs if log.actor_id}
    creators_map = {}
    if creator_ids:
        users = (await db.scalars(select(User).where(User.id.in_(creator_ids)))).all()
        creators_map = {u.id: u.full_name for u in users}

    items = []
    for log in logs:
        details = log.details or {}
        creator_name = creators_map.get(log.actor_id, "System / Admin")
        amount = float(details.get("amount_egp", 0.0))
        items.append({
            "id": str(log.id),
            "actor_id": str(log.actor_id) if log.actor_id else None,
            "actor_name": creator_name,
            "actor_role": log.actor_role,
            "transaction_id": details.get("transaction_id", str(log.id)),
            "target_email": details.get("target_email", ""),
            "target_user_id": details.get("target_user_id", ""),
            "amount_egp": amount,
            "platform_cut_egp": round(amount * 0.20, 2),
            "creator_cut_egp": round(amount * 0.80, 2),
            "note": str(details.get("note", "")),
            "created_at": log.created_at.isoformat(),
        })
    return items


@router.get(
    "/v1/admin/creators/ledger/export",
    status_code=status.HTTP_200_OK,
    summary="Export creator topup ledger as CSV (Admin only)",
)
async def admin_export_creator_ledger(
    admin: RequireAdmin,
    db: AsyncSession = Depends(get_db),
) -> Response:
    from sqlalchemy import desc, select
    from app.modules.audit.models import AuditLog
    from app.modules.identity.models import User
    import io, csv

    stmt = (
        select(AuditLog)
        .where(AuditLog.action == "wallet.admin_manual_topup")
        .order_by(desc(AuditLog.created_at))
        .limit(1000)
    )
    logs = (await db.scalars(stmt)).all()

    creator_ids = {log.actor_id for log in logs if log.actor_id}
    creators_map = {}
    if creator_ids:
        users = (await db.scalars(select(User).where(User.id.in_(creator_ids)))).all()
        creators_map = {u.id: u.full_name for u in users}

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Transaction ID",
        "Creator Name",
        "Student Email/Phone",
        "Gross Amount (EGP)",
        "Platform Share 20% (EGP)",
        "Creator Net 80% (EGP)",
        "Note / Booklet",
        "Timestamp",
    ])

    for log in logs:
        details = log.details or {}
        creator_name = creators_map.get(log.actor_id, "Admin")
        amount = float(details.get("amount_egp", 0.0))
        writer.writerow([
            details.get("transaction_id", str(log.id)),
            creator_name,
            details.get("target_email", ""),
            amount,
            round(amount * 0.20, 2),
            round(amount * 0.80, 2),
            details.get("note", ""),
            log.created_at.strftime("%Y-%m-%d %H:%M:%S"),
        ])

    csv_bytes = b"\xef\xbb\xbf" + output.getvalue().encode("utf-8")
    return Response(
        content=csv_bytes,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": "attachment; filename*=UTF-8''MedFighter_Creator_Sales_Ledger.csv",
        },
    )


