"""Request context: request-id + timing propagated through contextvars.

Backs §67 (request_id on every structured log line) and §41 (actor/ip/user
agent availability for audit records later).
"""

from __future__ import annotations

import time
import uuid
from contextvars import ContextVar

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
request_started_var: ContextVar[float] = ContextVar("request_started", default=0.0)


def new_request_id() -> str:
    return uuid.uuid4().hex


def elapsed_ms() -> int:
    return int((time.perf_counter() - request_started_var.get()) * 1000)
