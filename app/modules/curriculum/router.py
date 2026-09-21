"""Curriculum HTTP router."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.modules.curriculum.schemas import CurriculumFolderCreate, CurriculumFolderOut, CurriculumFolderUpdate
from app.modules.curriculum.service import create_folder, delete_folder, list_folders, update_folder
from app.modules.identity.deps import RequireAdmin

router = APIRouter(prefix="", tags=["curriculum"])


@router.get(
    "/v1/curriculum/folders",
    response_model=list[CurriculumFolderOut],
    status_code=status.HTTP_200_OK,
    summary="List curriculum modules/subjects/folders for a medical year",
)
async def get_curriculum_folders(
    response: Response,
    medical_year: int | None = Query(default=None, ge=1, le=6),
    parent_id: uuid.UUID | None = Query(default=None),
    all_descendants: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
) -> list[CurriculumFolderOut]:
    response.headers["Cache-Control"] = "no-cache, must-revalidate"
    return await list_folders(
        db, medical_year=medical_year, parent_id=parent_id, include_all_descendants=all_descendants
    )


@router.post(
    "/v1/curriculum/folders",
    response_model=CurriculumFolderOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a dynamic curriculum folder (Module, Subject, or Custom) (Admin only)",
)
async def admin_create_folder(
    payload: CurriculumFolderCreate,
    admin: RequireAdmin,
    db: AsyncSession = Depends(get_db),
) -> CurriculumFolderOut:
    return await create_folder(db, payload)


@router.put(
    "/v1/curriculum/folders/{folder_id}",
    response_model=CurriculumFolderOut,
    status_code=status.HTTP_200_OK,
    summary="Update a curriculum folder (Admin only)",
)
async def admin_update_folder(
    folder_id: uuid.UUID,
    payload: CurriculumFolderUpdate,
    admin: RequireAdmin,
    db: AsyncSession = Depends(get_db),
) -> CurriculumFolderOut:
    return await update_folder(db, folder_id, payload)


@router.delete(
    "/v1/curriculum/folders/{folder_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Delete a curriculum folder (Admin only)",
)
async def admin_delete_folder(
    folder_id: uuid.UUID,
    admin: RequireAdmin,
    db: AsyncSession = Depends(get_db),
) -> Response:
    await delete_folder(db, folder_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
