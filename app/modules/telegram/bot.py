"""Telegram long-poll background task.

Runs as an asyncio.Task inside the FastAPI lifespan. Calls getUpdates with a
30-second long-poll timeout, dispatches each update to handlers.py, then loops.
On cancellation (shutdown) it exits cleanly.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import async_sessionmaker

import app.modules.telegram.service as tg
from app.modules.telegram.handlers import configure_admin_usernames, dispatch

logger = logging.getLogger(__name__)


async def poll_forever(sessionmaker: async_sessionmaker) -> None:  # type: ignore[type-arg]
    """Continuously poll Telegram for updates and dispatch them."""
    logger.info("Telegram polling loop started")
    offset = 0
    while True:
        try:
            updates = await tg.get_updates(offset=offset, timeout=30)
            for upd in updates:
                offset = upd["update_id"] + 1
                try:
                    await dispatch(upd, sessionmaker)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Handler error for update %s: %s", upd.get("update_id"), exc)
        except asyncio.CancelledError:
            logger.info("Telegram polling loop cancelled — shutting down")
            raise
        except Exception as exc:  # noqa: BLE001
            # Network blip or Telegram error — back off 5 s then retry
            logger.warning("Telegram poll error: %s — retrying in 5 s", exc)
            await asyncio.sleep(5)


async def start_bot(
    sessionmaker: async_sessionmaker,  # type: ignore[type-arg]
    bot_token: str,
    admin_usernames: list[str],
) -> asyncio.Task:  # type: ignore[type-arg]
    """Initialise the service and spawn the polling loop as a background task."""
    tg.init_service(bot_token)
    configure_admin_usernames(*admin_usernames)
    task = asyncio.create_task(poll_forever(sessionmaker), name="telegram_polling")
    logger.info("Telegram bot task created — token prefix %s…", bot_token[:8])
    return task
