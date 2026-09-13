# =============================================================================
# Production Dockerfile for AI App Builder Backend
# =============================================================================
# Multi-stage build for smaller image size
# =============================================================================

# Build stage
FROM python:3.12-slim AS builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY requirements.lock .

# Install Python dependencies
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m venv /opt/venv && \
    /opt/venv/bin/pip install --require-hashes -r requirements.lock

# Production stage
FROM python:3.12-slim

WORKDIR /app

# Install runtime dependencies
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy Python packages from builder
COPY --from=builder /opt/venv /opt/venv

# Use the installed virtual environment for the unprivileged runtime.
ENV PATH=/opt/venv/bin:$PATH

# Copy application code
COPY . .

# Create non-root user for security
RUN useradd -m -u 1000 appuser && \
    mkdir -p /app/data /app/.cache && \
    chown -R appuser:appuser /app

USER appuser

# Environment
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    ENVIRONMENT=production \
    VOS_DEV_MEMORY_DIR=/app/data/memory \
    VOS_MEMORY_TENANT_ROOT=/app/data/memory-tenants/v1 \
    VOS3_LOCAL_DB_PATH=/app/data/vos3.db \
    VOS3_LOCAL_CHROMA_PATH=/app/data/chroma_db \
    VOS3_APP_DATA_DIR=/app/data \
    HF_HOME=/app/data/models \
    XDG_CACHE_HOME=/app/.cache \
    PORT=8000

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:${PORT}/health || exit 1

# Run server with production settings
# JSON memory fallback uses process-local locks; multiple workers need a
# transactional shared store before they can safely write the same tenant.
CMD ["sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port ${PORT} --workers 1 --loop uvloop --http httptools"]
