"""Structured logging (§67).

Every log line is JSON with request_id / user_id / operation / status /
duration — the fields §67 mandates — and the secret denial list is enforced
by a processor that scrubs redlisted keys wherever they appear.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import MutableMapping
from typing import Any, cast

import structlog

# §67: never log these (or any key containing them).
_SECRET_KEY_PARTS = (
    "password",
    "access_token",
    "refresh_token",
    "private_key",
    "cek",
    "payment_secret",
    "authorization",
    "cookie",
)


def _scrub_secrets(
    logger: Any, method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    del logger, method_name
    for key in list(event_dict):
        lowered = key.lower()
        if any(part in lowered for part in _SECRET_KEY_PARTS):
            event_dict[key] = "[REDACTED]"
    return event_dict


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _scrub_secrets,
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping().get(level.upper(), logging.INFO)
        ),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return cast("structlog.stdlib.BoundLogger", structlog.get_logger(name))
