"""Health endpoints (§73)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from sqlalchemy import text

from app.core.db import get_engine
from app.core.versioning import APP_VERSION

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "ok", "version": APP_VERSION}


@router.get("/health/ready")
async def readiness() -> dict[str, Any]:
    """Ready = can reach PostgreSQL. Falls back to 503 if not."""
    db_ok = False
    try:
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False
    status = "ok" if db_ok else "degraded"
    return {"status": status, "database": db_ok, "version": APP_VERSION}
