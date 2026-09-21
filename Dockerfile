FROM python:3.12-slim

WORKDIR /app

# Install system dependencies (curl for healthcheck)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Create user with UID 1000 (standard for Hugging Face Spaces & non-root containers)
RUN useradd -m -u 1000 user

# Install Python dependencies from requirements.txt
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY . .

# Set up storage and permissions
RUN mkdir -p /app/storage/uploads/pdfs /app/storage/proofs /app/storage/packages && \
    chmod +x /app/entrypoint.sh && \
    chown -R user:user /app

USER user

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=7860 \
    FIGHTERS_ENVIRONMENT=production

EXPOSE 7860

HEALTHCHECK --interval=20s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://127.0.0.1:${PORT}/v1/health || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]
