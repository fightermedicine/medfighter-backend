"""Identity security primitives (§5, §6).

- Modern Argon2id password hashing
- Short-lived JWT access tokens
- Cryptographically secure refresh token generation & hashing
"""

from __future__ import annotations

import hashlib
import os
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

# Argon2id hasher with RFC 9106 recommended interactive parameters
_hasher = PasswordHasher(
    time_cost=2,
    memory_cost=65536,  # 64 MiB
    parallelism=1,
    hash_len=32,
    salt_len=16,
)

# JWT Signing Secret (Defaults to environment-driven or fallback for tests)
_JWT_SECRET = os.environ.get(
    "FIGHTERS_JWT_SECRET", "dev-jwt-secret-do-not-use-in-prod-32bytesmin!!"
)
_JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 15
REFRESH_TOKEN_EXPIRE_DAYS = 30


def hash_password(password: str) -> str:
    """Hash a plaintext password using Argon2id."""
    return _hasher.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plaintext password against an Argon2id hash."""
    try:
        return _hasher.verify(hashed_password, plain_password)
    except (VerifyMismatchError, Exception):
        return False


def needs_rehash(hashed_password: str) -> bool:
    """Check if hash parameters are outdated and need upgrading."""
    return _hasher.check_needs_rehash(hashed_password)


def create_access_token(
    user_id: str,
    roles: list[str],
    device_id: str | None = None,
    expires_delta: timedelta | None = None,
) -> str:
    """Issue short-lived access credential with embedded user and roles."""
    now = datetime.now(UTC)
    expire = now + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    payload: dict[str, Any] = {
        "sub": user_id,
        "roles": roles,
        "jti": str(uuid.uuid4()),
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
    }
    if device_id:
        payload["dev"] = device_id
    return jwt.encode(payload, _JWT_SECRET, algorithm=_JWT_ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any] | None:
    """Decode and validate a JWT access token, returning payload or None."""
    try:
        payload = jwt.decode(token, _JWT_SECRET, algorithms=[_JWT_ALGORITHM])
        return payload
    except jwt.PyJWTError:
        return None


def generate_refresh_token() -> str:
    """Generate high-entropy random token string for refresh lifecycle."""
    return secrets.token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    """Compute SHA-256 digest of refresh token for database persistence."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
