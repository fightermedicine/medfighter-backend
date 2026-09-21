#!/usr/bin/env bash
set -e

echo "=================================================="
echo "  MEDFIGHTER PRODUCTION BACKEND INITIALIZATION   "
echo "=================================================="

# 1. Run database migrations to ensure Supabase schema is up-to-date
echo "[1/2] Checking & applying database migrations (Alembic)..."
if command -v alembic >/dev/null 2>&1; then
    alembic upgrade head || echo "[WARN] Alembic migrations reported an error or already at head."
else
    echo "[WARN] Alembic not found, skipping migration step."
fi

# 2. Launch production ASGI server (Uvicorn)
PORT="${PORT:-7860}"
WORKERS="${WORKERS:-2}"

echo "[2/2] Starting Uvicorn ASGI server on port ${PORT} with ${WORKERS} workers..."
exec uvicorn app.main:create_app --factory \
    --host 0.0.0.0 \
    --port "${PORT}" \
    --workers "${WORKERS}" \
    --proxy-headers \
    --forwarded-allow-ips "*"
