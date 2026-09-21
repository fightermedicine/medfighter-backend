"""Content and Annotation API router (§18, §19)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, status
from sqlalchemy import select

from app.common.deps import DbSession
from app.modules.content.models import ContentAnnotation
from app.modules.content.schemas import AnnotationResponse, CreateAnnotationRequest
from app.modules.identity.deps import RequireUser

router = APIRouter(prefix="/content", tags=["content"])


def _utc_now() -> datetime:
    return datetime.now(UTC)


@router.get("/{asset_id}/annotations", response_model=list[AnnotationResponse])
async def list_annotations(
    asset_id: uuid.UUID,
    db: DbSession,
    current_user: RequireUser,
) -> list[AnnotationResponse]:
    """Retrieve decoupled student annotation layer for a protected document."""
    query = (
        select(ContentAnnotation)
        .where(
            ContentAnnotation.content_asset_id == asset_id,
            ContentAnnotation.user_id == current_user.id,
        )
        .order_by(ContentAnnotation.page_number.asc(), ContentAnnotation.created_at.asc())
    )
    annotations = (await db.scalars(query)).all()
    return [AnnotationResponse.model_validate(a) for a in annotations]


@router.post(
    "/{asset_id}/annotations",
    response_model=AnnotationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_annotation(
    asset_id: uuid.UUID,
    payload: CreateAnnotationRequest,
    db: DbSession,
    current_user: RequireUser,
) -> AnnotationResponse:
    """Persist student highlight, ink stroke, text note, or bookmark decoupled from PDF."""
    now = _utc_now()
    annotation = ContentAnnotation(
        user_id=current_user.id,
        content_asset_id=asset_id,
        page_number=payload.page_number,
        annotation_type=payload.annotation_type,
        data_json=payload.data_json,
        created_at=now,
        updated_at=now,
    )
    db.add(annotation)
    await db.commit()
    await db.refresh(annotation)
    return AnnotationResponse.model_validate(annotation)
