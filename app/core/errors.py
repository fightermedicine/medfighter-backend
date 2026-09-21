"""Structured problem-details errors (§44) with secure-by-default handling."""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

PROBLEM_MEDIA_TYPE = "application/problem+json"

# Stable machine-readable error codes. Clients switch on `code`, never on text.
KNOWN_CODES = {
    "validation_error",
    "not_found",
    "unauthenticated",
    "forbidden",
    "conflict",
    "insufficient_funds",
    "idempotency_conflict",
    "device_limit_reached",
    "license_unavailable",
    "unsupported_app_version",
    "rate_limited",
    "internal_error",
    "google_auth_failed",
    "invalid_google_token",
    "google_auth_aud_mismatch",
    "google_auth_unavailable",
    "device_limit_exceeded",
    "invalid_refresh_token",
    # Administrative & Identity
    "user_not_found",
    "invalid_role",
    "cannot_demote_self",
    "last_super_admin",
    "user_not_admin",
    # Payments, Content & Wallet
    "invalid_file_type",
    "file_too_large",
    "reference_generation_failed",
    "invalid_state",
    "entitlement_conflict",
    "cannot_reject_approved",
    "invalid_amount",
    "course_not_found",
    "quiz_not_found",
    "deck_not_found",
    "cannot_delete_self",
    "insufficient_balance",
}


class ProblemError(Exception):
    """Raise anywhere in the app; the handler turns it into problem+json."""

    def __init__(
        self,
        status_code: int,
        code: str,
        detail: str,
        extras: dict[str, Any] | None = None,
    ) -> None:
        if code not in KNOWN_CODES:
            KNOWN_CODES.add(code)
        self.status_code = status_code
        self.code = code
        self.detail = detail
        self.extras = extras or {}
        super().__init__(detail)

    def to_response(self, request: Request, request_id: str) -> JSONResponse:
        body: dict[str, Any] = {
            "type": f"https://fighters.app/problems/{self.code}",
            "title": self.code,
            "status": self.status_code,
            "detail": self.detail,
            "instance": request.url.path,
            "request_id": request_id,
            **self.extras,
        }
        return JSONResponse(
            status_code=self.status_code, content=body, media_type=PROBLEM_MEDIA_TYPE
        )


class NotFound(ProblemError):
    def __init__(self, what: str) -> None:
        super().__init__(404, "not_found", f"{what} not found")


class Unauthenticated(ProblemError):
    def __init__(self, detail: str = "Authentication required") -> None:
        super().__init__(401, "unauthenticated", detail)


class Forbidden(ProblemError):
    def __init__(self, detail: str = "Not allowed") -> None:
        super().__init__(403, "forbidden", detail)


class Conflict(ProblemError):
    def __init__(self, detail: str) -> None:
        super().__init__(409, "conflict", detail)
