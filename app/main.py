"""FastAPI application factory (§42, §44).

Routers are registered per module. Cross-module calls go through service
interfaces, never direct table access (§43).
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.core.errors import PROBLEM_MEDIA_TYPE, ProblemError
from app.core.logging import configure_logging, get_logger
from app.core.middleware import RequestContextMiddleware
from app.modules.admin.router import router as admin_router
from app.modules.catalog.router import router as catalog_router
from app.modules.content.router import router as content_router
from app.modules.curriculum.router import router as curriculum_router
from app.modules.entitlement.router import router as entitlement_router
from app.modules.health.router import router as health_router
from app.modules.identity.router import router as identity_router
from app.modules.learning.router import router as learning_router
from app.modules.notifications.router import router as notifications_router
from app.modules.orders.router import router as orders_router
from app.modules.packages.router import router as packages_router
from app.modules.video.router import router as video_router
from app.modules.payments.router import router as payments_router
from app.modules.vouchers.router import router as vouchers_router
from app.modules.wallet.router import router as wallet_router
from app.modules.analytics.router import router as analytics_router
from app.modules.telegram.router import router as telegram_router
from app.modules.creator.router import router as creator_router

logger = get_logger("app")

# Framework-raised HTTPExceptions map onto the stable problem-code space.
_HTTP_CODE_MAP = {
    401: "unauthenticated",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    429: "rate_limited",
}


# Cloudflare Tunnel public URL (set via FIGHTERS_CLOUDFLARE_TUNNEL_URL env var)
_TUNNEL_URL = "https://7786c0e7-983e-495b-8cbc-4c1f832667e7.cfargotunnel.com"


@asynccontextmanager
async def _lifespan(app: FastAPI):  # type: ignore[misc]
    """Manage Telegram bot lifecycle.

    Production (environment=production):
      - Deletes any previous long-poll state
      - Registers a Telegram webhook at <TUNNEL_URL>/telegram/webhook
      - No background polling task needed — Telegram pushes to us

    Development / test:
      - Deletes any registered webhook
      - Starts long-poll loop as background asyncio.Task
    """
    import os

    settings = get_settings()
    _bot_task: asyncio.Task | None = None  # type: ignore[type-arg]

    try:
        from app.modules.admin.service import get_contact_info
        from app.modules.telegram.bot import start_bot
        import app.modules.telegram.service as tg
        from app.modules.telegram.handlers import configure_admin_usernames
        from app.modules.telegram.router import set_webhook_secret

        # Safe schema auto-update for QuestionBank exam_mode
        try:
            from sqlalchemy import text
            async with get_sessionmaker()() as db:
                await db.execute(text("ALTER TABLE question_banks ADD COLUMN IF NOT EXISTS exam_mode VARCHAR(32) DEFAULT 'PRACTICE';"))
                await db.execute(text("ALTER TABLE question_banks ADD COLUMN IF NOT EXISTS show_explanations BOOLEAN DEFAULT TRUE;"))
                await db.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS assigned_folder_id UUID;"))
                await db.commit()
        except Exception as e:  # noqa: BLE001
            logger.debug("Safe schema auto-update skipped: %s", e)

        # Fetch contact info
        try:
            async with asyncio.timeout(3.0):
                async with get_sessionmaker()() as db:
                    info = await get_contact_info(db)
            bot_token: str = info.get("telegram_bot_token", "")
            admin_1: str = info.get("admin_telegram_1", "")
            admin_2: str = info.get("admin_telegram_2", "")
        except Exception:  # noqa: BLE001
            bot_token = "8505734437:AAG9QSGJ87GtpW7qGngcAxZO6cWAHoE8g3w"
            admin_1 = "@Mohamed_Hamed_Samaha"
            admin_2 = "@Moh_gom3a"

        if not bot_token:
            logger.warning("No Telegram bot token — bot disabled")
        else:
            tg.init_service(bot_token)
            configure_admin_usernames(admin_1, admin_2)

            webhook_url = os.environ.get("FIGHTERS_TELEGRAM_WEBHOOK_URL", "").strip()

            if settings.environment == "test":
                logger.info("Test environment — Telegram bot skipped")
            elif webhook_url:
                # ── Webhook mode (explicitly configured public endpoint) ─────
                webhook_secret = os.environ.get(
                    "FIGHTERS_TELEGRAM_WEBHOOK_SECRET", "medfighter-webhook-2026"
                )
                set_webhook_secret(webhook_secret)
                logger.info("Configuring Telegram webhook: %s", webhook_url)
                data = await tg._post(  # noqa: SLF001
                    "setWebhook",
                    url=webhook_url,
                    secret_token=webhook_secret,
                    allowed_updates=["message"],
                    drop_pending_updates=False,
                )
                if data and data.get("ok"):
                    logger.info("Telegram webhook registered successfully ✅")
                else:
                    logger.warning("Webhook registration failed: %s — falling back to polling", data)
                    await tg._post("deleteWebhook", drop_pending_updates=False)  # noqa: SLF001
                    _bot_task = await start_bot(
                        get_sessionmaker(),
                        bot_token=bot_token,
                        admin_usernames=[admin_1, admin_2],
                    )
            else:
                # ── Long-poll mode (robust & reliable: works anywhere without inbound tunnels) ──
                logger.info("Starting Telegram bot in long-polling mode...")
                await tg._post("deleteWebhook", drop_pending_updates=False)  # noqa: SLF001
                _bot_task = await start_bot(
                    get_sessionmaker(),
                    bot_token=bot_token,
                    admin_usernames=[admin_1, admin_2],
                )

    except Exception as exc:  # noqa: BLE001
        logger.warning("Telegram bot startup failed: %s", exc)

    yield  # ← application runs here

    # Shutdown
    if _bot_task and not _bot_task.done():
        _bot_task.cancel()
        try:
            await _bot_task
        except asyncio.CancelledError:
            pass
    logger.info("Telegram lifecycle ended")


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title="Fighters API",
        version="0.1.0",
        lifespan=_lifespan,
        docs_url="/docs" if settings.environment != "production" else None,
        openapi_url="/openapi.json" if settings.environment != "production" else None,
    )
    app.add_middleware(GZipMiddleware, minimum_size=500)
    app.add_middleware(RequestContextMiddleware)

    app.include_router(health_router, prefix="/v1")
    app.include_router(identity_router, prefix="/v1")
    app.include_router(wallet_router, prefix="/v1")
    app.include_router(catalog_router, prefix="/v1")
    app.include_router(curriculum_router)
    app.include_router(orders_router, prefix="/v1")
    app.include_router(entitlement_router, prefix="/v1")
    app.include_router(learning_router, prefix="/v1")
    app.include_router(notifications_router)
    app.include_router(packages_router, prefix="/v1")
    app.include_router(content_router, prefix="/v1")
    app.include_router(video_router, prefix="/v1")
    app.include_router(vouchers_router)
    app.include_router(admin_router)
    app.include_router(payments_router, prefix="/v1")
    app.include_router(analytics_router, prefix="/v1")
    app.include_router(telegram_router, prefix="/v1")
    app.include_router(creator_router)

    _register_error_handlers(app)
    return app


def _register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        from app.core.context import request_id_var

        code = _HTTP_CODE_MAP.get(exc.status_code, "http_error")
        body = {
            "type": f"https://fighters.app/problems/{code}",
            "title": code,
            "status": exc.status_code,
            "detail": str(exc.detail),
            "instance": request.url.path,
            "request_id": request_id_var.get(),
        }
        return JSONResponse(
            status_code=exc.status_code,
            content=body,
            media_type=PROBLEM_MEDIA_TYPE,
            headers=getattr(exc, "headers", None) or {},
        )

    @app.exception_handler(ProblemError)
    async def problem_error_handler(request: Request, exc: ProblemError) -> JSONResponse:
        from app.core.context import request_id_var

        return exc.to_response(request, request_id_var.get())

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        from app.core.context import request_id_var

        body = {
            "type": "https://fighters.app/problems/validation_error",
            "title": "validation_error",
            "status": 422,
            "detail": "Request validation failed",
            "errors": exc.errors(),
            "instance": request.url.path,
            "request_id": request_id_var.get(),
        }
        return JSONResponse(status_code=422, content=body, media_type=PROBLEM_MEDIA_TYPE)


app = create_app()
