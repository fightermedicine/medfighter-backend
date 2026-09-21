"""Telegram webhook router — receives Telegram updates via HTTPS POST.

Telegram calls POST /telegram/webhook with each update when a webhook is set.
This is the production path (vs. long-polling in development).

The endpoint is deliberately unauthenticated by Telegram's standard (they push
to the URL, and security comes from the secret token header).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Header, HTTPException, Request, status

logger = logging.getLogger(__name__)

router = APIRouter(tags=["telegram"])

# Secret token set when registering the webhook (optional but recommended)
_WEBHOOK_SECRET: str = ""


def set_webhook_secret(secret: str) -> None:
    """Configure the expected X-Telegram-Bot-Api-Secret-Token header value."""
    global _WEBHOOK_SECRET  # noqa: PLW0603
    _WEBHOOK_SECRET = secret


@router.post("/telegram/webhook", status_code=status.HTTP_200_OK)
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict:
    """Receive a Telegram update via webhook and dispatch it asynchronously."""
    # Validate secret token if configured
    if _WEBHOOK_SECRET and x_telegram_bot_api_secret_token != _WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="Invalid webhook secret")

    try:
        update = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    # Dispatch asynchronously — respond 200 immediately so Telegram doesn't retry
    try:
        from app.core.db import get_sessionmaker
        from app.modules.telegram.handlers import dispatch

        import asyncio
        asyncio.create_task(dispatch(update, get_sessionmaker()))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Webhook dispatch error: %s", exc)

    return {"ok": True}
