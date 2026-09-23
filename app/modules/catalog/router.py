"""Catalog API router (§10, §11, §17)."""

from __future__ import annotations

from typing import Any
import uuid

from fastapi import APIRouter, Request, Response

from app.common.deps import DbSession
from app.modules.catalog.schemas import CreateProductRequest, ProductPreviewResponse, ProductResponse
from app.modules.catalog.service import create_product, get_product, get_product_preview, list_products
from app.modules.identity.deps import OptionalUser, RequireAdmin
from app.services.r2_storage import R2StorageService

router = APIRouter(prefix="/catalog", tags=["catalog"])


@router.get("/products", response_model=list[ProductResponse])
async def get_products(
    response: Response,
    db: DbSession,
    medical_year: int | None = None,
    folder_id: uuid.UUID | None = None,
    current_user: OptionalUser = None,
) -> list[ProductResponse]:
    """Public catalog endpoint to list active products optionally filtered by medical year & folder."""
    response.headers["Cache-Control"] = "public, max-age=60, stale-while-revalidate=300"
    if current_user:
        is_admin = any(ur.role_id in ("ADMIN", "SUPER_ADMIN") for ur in current_user.roles)
        if not is_admin:
            medical_year = current_user.medical_year
    return await list_products(db, medical_year=medical_year, folder_id=folder_id)


@router.get("/products/{product_id}", response_model=ProductResponse)
async def get_product_by_id(
    product_id: uuid.UUID,
    db: DbSession,
) -> ProductResponse:
    """Public catalog endpoint to view product details."""
    return await get_product(db, product_id)


@router.get("/products/{product_id}/preview", response_model=ProductPreviewResponse)
async def get_preview(
    product_id: uuid.UUID,
    db: DbSession,
) -> ProductPreviewResponse:
    """Public endpoint to preview product sample pages and excerpt before purchase."""
    return await get_product_preview(db, product_id)


@router.get("/products/{product_id}/thumbnail")
async def get_thumbnail(
    product_id: uuid.UUID,
    request: Request,
    db: DbSession,
) -> Response:
    """Public endpoint to serve raw binary thumbnail with global edge caching and conditional 304 support."""
    from app.modules.catalog.service import get_product_thumbnail_bytes
    img_bytes, media_type = await get_product_thumbnail_bytes(db, product_id)
    etag = f'"{product_id}_{len(img_bytes)}"'
    cache_control = "public, max-age=86400, stale-while-revalidate=604800, immutable"

    client_etag = request.headers.get("if-none-match")
    if client_etag and client_etag.strip() == etag:
        return Response(
            status_code=304,
            headers={
                "Cache-Control": cache_control,
                "ETag": etag,
            },
        )

    return Response(
        content=img_bytes,
        media_type=media_type,
        headers={
            "Cache-Control": cache_control,
            "ETag": etag,
        },
    )




@router.post("/products", response_model=ProductResponse, status_code=201)
async def admin_create_product(
    request: Request,
    payload: CreateProductRequest,
    current_admin: RequireAdmin,
    db: DbSession,
) -> ProductResponse:
    """Admin endpoint to create a new commercial product."""
    ip_address = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")
    return await create_product(
        db,
        request=payload,
        admin_id=current_admin.id,
        ip_address=ip_address,
        user_agent=user_agent,
    )


@router.get("/courses/{course_id}/curriculum")
async def get_course_curriculum(
    course_id: uuid.UUID,
    db: DbSession,
) -> list[dict[str, Any]]:
    """Public/Student endpoint to list curriculum items for a course."""
    from app.modules.admin.service import list_course_curriculum_items
    return await list_course_curriculum_items(db, course_id=course_id)


@router.get("/packages/{package_id}/download-url")
async def get_package_download_url(
    package_id: str,
) -> dict[str, Any]:
    """Provides an authenticated Cloudflare R2 streaming URL for encrypted course packages."""
    service = R2StorageService()
    return service.generate_download_url(package_id=package_id)


@router.get("/packages/{package_id}/stream")
async def stream_catalog_package(
    package_id: str,
    db: DbSession,
):
    """Fallback stream endpoint under /v1/catalog/packages/{package_id}/stream."""
    from app.modules.packages.router import stream_package_file
    return await stream_package_file(package_id=package_id, db=db)

