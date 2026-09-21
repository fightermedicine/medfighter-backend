# ==============================================================================
# MEDFIGHTER PRODUCTION BACKEND DOCKERFILE
# Linux ASGI (FastAPI + Uvicorn) with C-extensions (asyncpg, argon2, cryptography)
# Target: Western Europe (eu-west-1 / eu-central-1) adjacent to Supabase
# ==============================================================================

FROM python:3.12-slim-bookworm AS builder

WORKDIR /app

# Install native compilation toolchain required for asyncpg, argon2-cffi, cryptography
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    python3-dev \
    libpq-dev \
    libffi-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install build dependencies
COPY pyproject.toml README.md ./
RUN pip install --no-cache-dir --upgrade pip hatchling wheel
RUN pip install --no-cache-dir .

# ==============================================================================
# Final Runtime Stage
# ==============================================================================
FROM python:3.12-slim-bookworm AS runtime

WORKDIR /app

# Install runtime dependencies only (curl for healthchecks)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy installed site-packages from builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Create unprivileged application user
RUN groupadd -g 1001 appuser && \
    useradd -u 1001 -g appuser -s /bin/bash -m appuser

# Copy application source and configurations
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini
COPY worker.py ./worker.py
COPY entrypoint.sh ./entrypoint.sh

# Ensure storage directories exist with write permissions
RUN mkdir -p /app/storage/uploads/pdfs /app/storage/proofs /app/storage/packages && \
    chmod +x /app/entrypoint.sh && \
    chown -R appuser:appuser /app

USER appuser

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000 \
    FIGHTERS_ENVIRONMENT=production

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://127.0.0.1:${PORT}/v1/health || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]
