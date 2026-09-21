"""HTTP middleware: request-id propagation and structured access logs (§67)."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.context import elapsed_ms, new_request_id, request_id_var, request_started_var
from app.core.logging import get_logger

logger = get_logger("http")


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assigns/propagates X-Request-ID, extracts Cloudflare edge IP, and emits structured access logs (§67)."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # Extract or correlate Request ID / CF-Ray
        incoming = (
            request.headers.get("X-Request-ID")
            or request.headers.get("CF-Ray")
            or request.headers.get("X-Cloudflare-Ray")
            or ""
        )
        request_id = incoming if 8 <= len(incoming) <= 128 else new_request_id()
        request_id_var.set(request_id)
        request_started_var.set(time.perf_counter())

        # Extract authentic client IP behind Cloudflare edge proxy
        client_ip = (
            request.headers.get("CF-Connecting-IP")
            or request.headers.get("X-Real-IP")
            or (request.headers.get("X-Forwarded-For", "").split(",")[0].strip() or None)
            or (request.client.host if request.client else "unknown")
        )
        country = request.headers.get("CF-IPCountry") or request.headers.get("X-Client-Country")

        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id

        cf_ray = request.headers.get("CF-Ray") or request.headers.get("X-Cloudflare-Ray")
        if cf_ray:
            response.headers["X-Cloudflare-Ray"] = cf_ray

        try:
            logger.info(
                "http_request",
                request_id=request_id,
                method=request.method,
                path=request.url.path,
                status=response.status_code,
                duration_ms=elapsed_ms(),
                client_ip=client_ip,
                country=country,
            )
        except Exception:
            pass
        return response
