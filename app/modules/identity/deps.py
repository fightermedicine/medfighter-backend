"""Authentication and Authorization dependencies (§5, §6, §7).

Enforces server-authoritative RBAC and authenticated user context.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Annotated

import time
from fastapi import Depends, Header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.core.db import get_db
from app.core.errors import ProblemError
from app.modules.identity.models import User, UserRole
from app.modules.identity.security import decode_access_token


_USER_CACHE: dict[uuid.UUID, tuple[User, float]] = {}
_USER_CACHE_TTL = 15.0  # 15s cache to deduplicate simultaneous requests


def invalidate_user_cache(user_id: uuid.UUID | None = None) -> None:
    if user_id:
        _USER_CACHE.pop(user_id, None)
    else:
        _USER_CACHE.clear()


class UnauthorizedError(ProblemError):
    def __init__(self, detail: str = "Authentication required.") -> None:
        super().__init__(
            status_code=401,
            code="unauthenticated",
            detail=detail,
        )


class ForbiddenError(ProblemError):
    def __init__(self, detail: str = "Insufficient permissions.") -> None:
        super().__init__(
            status_code=403,
            code="forbidden",
            detail=detail,
        )


async def get_current_user(
    authorization: Annotated[str | None, Header()] = None,
    db: AsyncSession = Depends(get_db),
) -> User:
    """Extract and validate the current authenticated user from Bearer token."""
    if not authorization or not authorization.startswith("Bearer "):
        raise UnauthorizedError("Missing or invalid Bearer token header.")

    token = authorization.removeprefix("Bearer ").strip()
    payload = decode_access_token(token)
    if not payload or "sub" not in payload:
        raise UnauthorizedError("Token is invalid or has expired.")

    try:
        user_uuid = uuid.UUID(payload["sub"])
    except ValueError as err:
        raise UnauthorizedError("Invalid user identity in token.") from err

    now = time.monotonic()
    cached = _USER_CACHE.get(user_uuid)
    if cached and (now - cached[1] < _USER_CACHE_TTL) and cached[0].is_active:
        return cached[0]

    query = (
        select(User)
        .where(User.id == user_uuid)
        .options(joinedload(User.roles).joinedload(UserRole.role))
    )
    user = await db.scalar(query)
    if not user or not user.is_active:
        _USER_CACHE.pop(user_uuid, None)
        raise UnauthorizedError("User is inactive or no longer exists.")

    _USER_CACHE[user_uuid] = (user, now)
    return user


def require_roles(*allowed_roles: str) -> Callable[[User], User]:
    """Dependency factory checking that the authenticated user possesses an allowed role."""

    def role_checker(user: User = Depends(get_current_user)) -> User:
        user_roles = {ur.role_id for ur in user.roles}
        # Super admin always bypasses standard role checks
        if "SUPER_ADMIN" in user_roles:
            return user
        if not user_roles.intersection(allowed_roles):
            raise ForbiddenError(f"Operation requires one of the following roles: {allowed_roles}")
        return user

    return role_checker


# Convenient role dependencies
RequireUser = Annotated[User, Depends(get_current_user)]
RequireAdmin = Annotated[User, Depends(require_roles("ADMIN", "SUPER_ADMIN"))]
RequireSuperAdmin = Annotated[User, Depends(require_roles("SUPER_ADMIN"))]
RequireCreator = Annotated[User, Depends(require_roles("CREATOR", "ADMIN", "SUPER_ADMIN"))]
RequireModerator = Annotated[User, Depends(require_roles("MODERATOR", "ADMIN", "SUPER_ADMIN"))]
