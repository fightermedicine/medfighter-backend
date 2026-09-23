"""Curriculum domain service with zero-N+1 batching and idempotent deletions."""

from __future__ import annotations

import uuid
from collections import defaultdict

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFound
from app.modules.catalog.models import Product
from app.modules.curriculum.models import CurriculumFolder
from app.modules.curriculum.schemas import (
    CurriculumFolderCreate,
    CurriculumFolderOut,
    CurriculumFolderUpdate,
)
from app.modules.learning.models import Deck, QuestionBank


async def list_folders(
    db: AsyncSession,
    medical_year: int | None = None,
    parent_id: uuid.UUID | None = None,
    include_all_descendants: bool = False,
) -> list[CurriculumFolderOut]:
    """Fetch curriculum folders in a single batched query (O(1) roundtrips, zero N+1)."""
    # Single batched query for all folders matching the year
    stmt = select(CurriculumFolder).order_by(
        CurriculumFolder.medical_year,
        CurriculumFolder.order_index,
        CurriculumFolder.name,
    )
    if medical_year is not None:
        stmt = stmt.where(CurriculumFolder.medical_year == medical_year)

    all_folders = (await db.scalars(stmt)).all()

    # Index children by parent_id in memory
    children_by_parent: dict[uuid.UUID, list[CurriculumFolder]] = defaultdict(list)
    top_level: list[CurriculumFolder] = []

    for f in all_folders:
        if f.parent_id is not None:
            children_by_parent[f.parent_id].append(f)
        else:
            top_level.append(f)

    # Filter roots based on query parameters
    if include_all_descendants:
        roots = all_folders
    elif parent_id is not None:
        roots = [f for f in all_folders if f.parent_id == parent_id]
    else:
        roots = top_level

    results: list[CurriculumFolderOut] = []
    for f in roots:
        child_entities = children_by_parent.get(f.id, [])
        children_out = [
            CurriculumFolderOut(
                id=c.id,
                medical_year=c.medical_year,
                parent_id=c.parent_id,
                name=c.name,
                folder_type=c.folder_type,
                icon=c.icon,
                order_index=c.order_index,
                created_at=c.created_at,
                updated_at=c.updated_at,
                child_count=len(children_by_parent.get(c.id, [])),
                children=[],
            )
            for c in child_entities
        ]
        results.append(
            CurriculumFolderOut(
                id=f.id,
                medical_year=f.medical_year,
                parent_id=f.parent_id,
                name=f.name,
                folder_type=f.folder_type,
                icon=f.icon,
                order_index=f.order_index,
                created_at=f.created_at,
                updated_at=f.updated_at,
                child_count=len(children_out),
                children=children_out,
            )
        )
    return results


async def create_folder(
    db: AsyncSession,
    payload: CurriculumFolderCreate,
) -> CurriculumFolderOut:
    """Create a new curriculum folder (Module, Subject, or Custom folder)."""
    if payload.parent_id is not None:
        parent = await db.scalar(
            select(CurriculumFolder).where(CurriculumFolder.id == payload.parent_id)
        )
        if not parent:
            raise NotFound("Parent folder")
        medical_year = parent.medical_year
    else:
        medical_year = payload.medical_year

    folder = CurriculumFolder(
        medical_year=medical_year,
        parent_id=payload.parent_id,
        name=payload.name.strip(),
        folder_type=payload.folder_type,
        icon=payload.icon,
        order_index=payload.order_index,
    )
    db.add(folder)
    await db.commit()
    await db.refresh(folder)

    return CurriculumFolderOut(
        id=folder.id,
        medical_year=folder.medical_year,
        parent_id=folder.parent_id,
        name=folder.name,
        folder_type=folder.folder_type,
        icon=folder.icon,
        order_index=folder.order_index,
        created_at=folder.created_at,
        updated_at=folder.updated_at,
        child_count=0,
    )


async def update_folder(
    db: AsyncSession,
    folder_id: uuid.UUID,
    payload: CurriculumFolderUpdate,
) -> CurriculumFolderOut:
    """Update a curriculum folder."""
    folder = await db.scalar(
        select(CurriculumFolder).where(CurriculumFolder.id == folder_id)
    )
    if not folder:
        raise NotFound("Curriculum folder")

    if payload.name is not None:
        folder.name = payload.name.strip()
    if payload.parent_id is not None:
        folder.parent_id = payload.parent_id
    if payload.folder_type is not None:
        folder.folder_type = payload.folder_type
    if payload.icon is not None:
        folder.icon = payload.icon
    if payload.order_index is not None:
        folder.order_index = payload.order_index

    await db.commit()
    await db.refresh(folder)

    cnt = await db.scalar(
        select(func.count())
        .select_from(CurriculumFolder)
        .where(CurriculumFolder.parent_id == folder.id)
    )

    return CurriculumFolderOut(
        id=folder.id,
        medical_year=folder.medical_year,
        parent_id=folder.parent_id,
        name=folder.name,
        folder_type=folder.folder_type,
        icon=folder.icon,
        order_index=folder.order_index,
        created_at=folder.created_at,
        updated_at=folder.updated_at,
        child_count=cnt or 0,
    )


async def delete_folder(
    db: AsyncSession,
    folder_id: uuid.UUID,
) -> None:
    """Idempotently delete a curriculum folder and all descendant folders, unlinking attached content."""
    folder = await db.scalar(
        select(CurriculumFolder).where(CurriculumFolder.id == folder_id)
    )
    if not folder:
        # Idempotent deletion: if folder is already removed from DB, clean up any lingering references and return
        await db.execute(
            update(Product).where(Product.folder_id == folder_id).values(folder_id=None)
        )
        await db.execute(
            update(QuestionBank).where(QuestionBank.folder_id == folder_id).values(folder_id=None)
        )
        await db.execute(
            update(Deck).where(Deck.folder_id == folder_id).values(folder_id=None)
        )
        await db.commit()
        return

    # Find all child IDs to ensure content unlinking before folder removal
    child_ids = (
        await db.scalars(
            select(CurriculumFolder.id).where(CurriculumFolder.parent_id == folder.id)
        )
    ).all()
    all_target_ids = [folder.id, *child_ids]

    await db.execute(
        update(Product).where(Product.folder_id.in_(all_target_ids)).values(folder_id=None)
    )
    await db.execute(
        update(QuestionBank).where(QuestionBank.folder_id.in_(all_target_ids)).values(folder_id=None)
    )
    await db.execute(
        update(Deck).where(Deck.folder_id.in_(all_target_ids)).values(folder_id=None)
    )

    await db.delete(folder)
    await db.commit()


async def get_folder_and_descendant_ids(
    db: AsyncSession, folder_id: uuid.UUID
) -> list[uuid.UUID]:
    """Get the target folder ID along with all of its recursive descendant folder IDs."""
    all_folders = (await db.scalars(select(CurriculumFolder))).all()
    children_by_parent: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    for f in all_folders:
        if f.parent_id is not None:
            children_by_parent[f.parent_id].append(f.id)

    result = [folder_id]
    queue = [folder_id]
    while queue:
        curr = queue.pop(0)
        for child_id in children_by_parent.get(curr, []):
            if child_id not in result:
                result.append(child_id)
                queue.append(child_id)
    return result

