"""Idempotency contract (§46).

All money-changing operations require an Idempotency-Key. This module owns
the semantics: extraction/validation and payload fingerprinting, so a replayed
key with a *different* payload can be detected (idempotency_conflict) instead
of silently returning a mismatched response.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Annotated

from fastapi import Header

from app.core.errors import Conflict, Unauthenticated

_MIN_KEY_LEN = 16
_MAX_KEY_LEN = 128


@dataclass(frozen=True, slots=True)
class IdempotencyKey:
    raw: str
    payload_sha256: str


def fingerprint_body(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def require_idempotency_key(
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> IdempotencyKey:
    """FastAPI dependency for money-changing routes (§46)."""
    if not idempotency_key or not idempotency_key.strip():
        raise Unauthenticated("Missing Idempotency-Key header (required for this operation)")
    key = idempotency_key.strip()
    if not (_MIN_KEY_LEN <= len(key) <= _MAX_KEY_LEN):
        raise Conflict(f"Idempotency-Key must be {_MIN_KEY_LEN}-{_MAX_KEY_LEN} characters")
    return IdempotencyKey(raw=key, payload_sha256=fingerprint_body(key.encode()))
