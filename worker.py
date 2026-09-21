"""Background worker entry point (§50).

v1 architecture: PostgreSQL-backed queue with FOR UPDATE SKIP LOCKED, drained
by this separate process — no Redis. Jobs land in Phase 3+ (packaging, OCR,
reconciliation); this stub keeps the process model real from day one.
"""

from __future__ import annotations

import asyncio

from app.core.logging import configure_logging, get_logger

logger = get_logger("worker")

POLL_INTERVAL_SECONDS = 2.0


async def main() -> None:
    configure_logging("INFO")
    logger.info("worker_starting", queue="postgres_skip_locked")
    while True:
        # TODO(Phase 3+): claim job via FOR UPDATE SKIP LOCKED and dispatch.
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    asyncio.run(main())
