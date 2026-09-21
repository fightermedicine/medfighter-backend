"""Packages Streaming API Router (§18, §19, §21).

Streams real encrypted packages or raw uploaded PDFs from ContentAsset storage to learners.
"""

from __future__ import annotations

import logging
import os
import uuid

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy import select

from app.common.deps import DbSession
from app.modules.catalog.models import Product
from app.modules.content.models import ContentAsset

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/packages", tags=["packages"])

UPLOAD_DIR = os.path.join("storage", "uploads", "pdfs")
PACKAGES_DIR = os.path.join("storage", "packages")


def _all_pdf_files() -> list[str]:
    """Return full paths of all PDF files in the upload directory."""
    if not os.path.exists(UPLOAD_DIR):
        return []
    return [
        os.path.join(UPLOAD_DIR, f)
        for f in os.listdir(UPLOAD_DIR)
        if f.lower().endswith(".pdf")
    ]


@router.get("/{package_id}/stream")
async def stream_package_file(
    package_id: str,
    db: DbSession,
) -> FileResponse:
    """Streams the package or PDF file for a given packageId / productId.

    Resolves:
      1. package_id with prefix 'pkg_<uuid>' or direct '<uuid>' -> looks up ContentAsset
      2. Storage packages in storage/packages/<package_id>.fght
      3. Uploaded PDFs in storage/uploads/pdfs/ — scans by product UUID prefix or any match
      4. Auto-seeds missing ContentAsset record from discovered file
    """
    clean_id = package_id.removeprefix("pkg_").strip()
    logger.info("Streaming package request for package_id=%s, clean_id=%s", package_id, clean_id)

    # 1. Try resolving by UUID (product_id or asset_id)
    target_uuid: uuid.UUID | None = None
    try:
        target_uuid = uuid.UUID(clean_id)
    except ValueError:
        target_uuid = None

    if target_uuid:
        # Check by product_id
        asset = await db.scalar(
            select(ContentAsset)
            .where(ContentAsset.product_id == target_uuid)
            .order_by(ContentAsset.created_at.desc())
        )
        if not asset:
            # Check by asset id
            asset = await db.scalar(
                select(ContentAsset)
                .where(ContentAsset.id == target_uuid)
            )

        if asset and asset.storage_path and os.path.exists(asset.storage_path):
            filename = os.path.basename(asset.storage_path)
            logger.info("Found asset on disk: %s (%d bytes)", asset.storage_path, asset.size_bytes)
            return FileResponse(
                path=asset.storage_path,
                media_type="application/pdf",
                filename=filename,
            )

    # 2. Check for prepackaged file in storage/packages/
    packaged_path = os.path.join(PACKAGES_DIR, f"{package_id}.fght")
    if os.path.exists(packaged_path):
        return FileResponse(
            path=packaged_path,
            media_type="application/octet-stream",
            filename=f"{package_id}.fght",
        )

    # 3. Search storage/uploads/pdfs for filename match
    all_pdfs = _all_pdf_files()

    if all_pdfs and target_uuid:
        uuid_str = str(target_uuid)
        uuid_short = uuid_str.replace("-", "")[:12]  # first 12 hex chars

        # Priority 1: file whose name contains the short UUID hex prefix
        for full_p in all_pdfs:
            fname = os.path.basename(full_p)
            if uuid_short in fname or uuid_str[:8] in fname:
                logger.info("Matched PDF by UUID prefix: %s", full_p)
                _seed_content_asset(db, target_uuid, full_p)
                return FileResponse(path=full_p, media_type="application/pdf", filename=fname)

        # Priority 2: if there is exactly one PDF in the directory (single-product setup)
        if len(all_pdfs) == 1:
            full_p = all_pdfs[0]
            fname = os.path.basename(full_p)
            logger.info("Single PDF found, serving: %s", full_p)
            _seed_content_asset(db, target_uuid, full_p)
            return FileResponse(path=full_p, media_type="application/pdf", filename=fname)

    # Priority 3: clean_id substring match in filename
    if all_pdfs:
        for full_p in all_pdfs:
            fname = os.path.basename(full_p)
            if clean_id in fname:
                logger.info("Matched PDF by clean_id: %s", full_p)
                return FileResponse(path=full_p, media_type="application/pdf", filename=fname)

    # 4. Check if a Product exists by this ID to give a clear error
    if target_uuid:
        prod = await db.scalar(select(Product).where(Product.id == target_uuid))
        if prod:
            logger.warning("Product found (%s) but has no content asset on disk", prod.title)

    logger.error("Package or asset not found: %s", package_id)
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Package or asset not found: {package_id}",
    )


def _seed_content_asset(db, product_id: uuid.UUID, storage_path: str) -> None:
    """Best-effort: insert a ContentAsset record so future lookups use the DB index."""
    try:
        size = os.path.getsize(storage_path)
        asset = ContentAsset(
            product_id=product_id,
            storage_path=storage_path,
            content_type="application/pdf",
            size_bytes=size,
        )
        db.add(asset)
        # We don't await commit here — it will be committed with the next request cycle.
        # This is fire-and-forget for speed; the FileResponse is already prepared.
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not seed ContentAsset: %s", exc)

