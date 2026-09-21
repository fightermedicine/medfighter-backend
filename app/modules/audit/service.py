"""Audit logging service (§8, §41).

Append-only recording of security-sensitive operations.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.models import AuditLog, SecurityEvent


async def record_audit_log(
    session: AsyncSession,
    *,
    action: str,
    resource_type: str,
    actor_id: uuid.UUID | None = None,
    actor_role: str | None = None,
    resource_id: str | None = None,
    details: dict[str, Any] | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    result: str = "SUCCESS",
) -> AuditLog:
    """Record an immutable audit log entry."""
    entry = AuditLog(
        actor_id=actor_id,
        actor_role=actor_role,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        details=details,
        ip_address=ip_address,
        user_agent=user_agent,
        result=result,
    )
    session.add(entry)
    return entry


async def record_security_event(
    session: AsyncSession,
    *,
    event_type: str,
    severity: str = "INFO",
    actor_id: uuid.UUID | None = None,
    details: dict[str, Any] | None = None,
    ip_address: str | None = None,
) -> SecurityEvent:
    """Record a security anomaly or critical event."""
    event = SecurityEvent(
        event_type=event_type,
        severity=severity,
        actor_id=actor_id,
        details=details,
        ip_address=ip_address,
    )
    session.add(event)
    return event
