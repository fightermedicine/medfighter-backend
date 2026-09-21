"""Catalog domain service (§10, §11, §17, §64).

Handles product catalog management with server-authoritative pricing.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.errors import NotFound
from app.core.money import egp_to_piastres
from app.modules.audit.service import record_audit_log
from app.modules.catalog.models import Bundle, Product, ProductVersion
from app.modules.catalog.schemas import (
    BundleItemOut,
    BundleOut,
    CreateProductRequest,
    PriceRuleOut,
    ProductPreviewResponse,
    ProductResponse,
)


def _sanitize_list_preview_data(preview_data: str | None) -> str | None:
    """Pass through preview_data for list payloads.

    URLs and base64 data URIs are both preserved so thumbnails
    render correctly on the home screen product cards.
    """
    if not preview_data:
        return None
    return preview_data.strip() or None


def _to_product_response(p: Product, is_list: bool = False) -> ProductResponse:
    price_rules = [
        PriceRuleOut(min_quantity=r.min_quantity, discount_percent=r.discount_percent)
        for r in getattr(p, "price_rules", [])
    ]
    bundle_out = None
    if getattr(p, "bundle", None):
        bundle_out = BundleOut(
            id=p.bundle.id,
            title=p.bundle.title,
            items=[
                BundleItemOut(item_product_id=bi.item_product_id, order_index=bi.order_index)
                for bi in getattr(p.bundle, "items", [])
            ],
        )
    raw_preview = getattr(p, "preview_data", None)
    preview = _sanitize_list_preview_data(raw_preview) if is_list else raw_preview
    return ProductResponse(
        id=p.id,
        title=p.title,
        description=p.description,
        price_piastres=p.price_piastres,
        price_egp=p.price_piastres / 100.0,
        currency=p.currency,
        category=p.category,
        product_type=p.product_type,
        medical_year=getattr(p, "medical_year", 1) or 1,
        folder_id=getattr(p, "folder_id", None),
        preview_data=preview,
        is_active=p.is_active,
        created_at=p.created_at,
        price_rules=price_rules,
        bundle=bundle_out,
    )


async def create_product(
    db: AsyncSession,
    request: CreateProductRequest,
    admin_id: uuid.UUID,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> ProductResponse:
    """Create a new product with authoritative integer-piastres pricing."""
    piastres = egp_to_piastres(request.price_egp)
    product = Product(
        title=request.title,
        description=request.description,
        price_piastres=int(piastres),
        currency="EGP",
        category=request.category,
        product_type=request.product_type,
        medical_year=request.medical_year,
        folder_id=request.folder_id,
        preview_data=request.preview_data,
        is_active=True,
    )
    db.add(product)
    await db.flush()

    # Create initial version record (§32)
    version = ProductVersion(
        product_id=product.id,
        version_number=1,
        changelog="Initial release",
    )
    db.add(version)
    await db.flush()

    await record_audit_log(
        db,
        action="catalog.product_created",
        resource_type="product",
        actor_id=admin_id,
        actor_role="ADMIN",
        resource_id=str(product.id),
        details={
            "title": product.title,
            "price_piastres": product.price_piastres,
            "medical_year": product.medical_year,
        },
        ip_address=ip_address,
        user_agent=user_agent,
    )

    await db.commit()
    await db.refresh(product)

    return _to_product_response(product)


async def list_active_products(
    db: AsyncSession,
    medical_year: int | None = None,
    folder_id: uuid.UUID | None = None,
) -> list[ProductResponse]:
    """List all active published products with price rules, bundles, and year filtering."""
    query = (
        select(Product)
        .options(
            selectinload(Product.price_rules),
            selectinload(Product.bundle).selectinload(Bundle.items),
        )
        .where(Product.is_active.is_(True))
    )

    if medical_year is not None:
        query = query.where(Product.medical_year == medical_year)
    if folder_id is not None:
        query = query.where(Product.folder_id == folder_id)

    query = query.order_by(Product.created_at.desc())
    products = (await db.scalars(query)).all()
    return [_to_product_response(p, is_list=True) for p in products]


async def get_product_by_id(db: AsyncSession, product_id: uuid.UUID) -> Product:
    """Retrieve product or raise 404."""
    query = (
        select(Product)
        .options(
            selectinload(Product.price_rules),
            selectinload(Product.bundle).selectinload(Bundle.items),
        )
        .where(Product.id == product_id)
    )
    product = await db.scalar(query)
    if not product:
        raise NotFound("Product")
    return product


async def get_product(db: AsyncSession, product_id: uuid.UUID) -> ProductResponse:
    """Retrieve product response or raise 404."""
    p = await get_product_by_id(db, product_id)
    return _to_product_response(p)


async def get_product_preview(db: AsyncSession, product_id: uuid.UUID) -> ProductPreviewResponse:
    """Return student preview for a product (PDF sample pages, syllabus summary, table of contents)."""
    p = await get_product_by_id(db, product_id)
    excerpt = p.description
    if len(excerpt) > 250:
        excerpt = excerpt[:247] + "..."
    sample_pages = [
        f"Preview Page 1: Introduction & Chapter Outline for {p.title}",
        f"Preview Page 2: Key Clinical High-Yield Concepts & Diagrams",
    ]
    return ProductPreviewResponse(
        id=p.id,
        title=p.title,
        description=p.description,
        product_type=p.product_type,
        medical_year=p.medical_year,
        price_egp=p.price_piastres / 100.0,
        preview_excerpt=excerpt,
        sample_pages=sample_pages,
        page_count=2,
    )


list_products = list_active_products
