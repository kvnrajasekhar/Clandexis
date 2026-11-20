# ==============================================================================
# Clandexis Invisible ERP — Webhook Service
# Multi-stage build for Google Cloud Run (asia-south1)
# ==============================================================================

# ---- Builder Stage ----
FROM python:3.12-slim AS builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir --prefix=/install -r requirements.txt


# ---- Runtime Stage ----
FROM python:3.12-slim AS runtime

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application source
COPY config.py ./config.py
COPY models/ ./models/
COPY inventory/ ./inventory/
COPY webhook-service/ ./webhook-service/
COPY agent-orchestrator/ ./agent-orchestrator/
COPY start.sh ./start.sh

# Create Python module symlinks for hyphenated directory names
RUN ln -s /app/agent-orchestrator /app/agent_orchestrator && \
    ln -s /app/webhook-service /app/webhook_service

# Cloud Run requires the app to listen on $PORT (default 8080)
ENV PORT=8080
ENV HOST=0.0.0.0
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV ENVIRONMENT=production

# Non-root user for security
RUN addgroup --gid 1001 appgroup && \
    adduser --uid 1001 --gid 1001 --disabled-password --gecos "" appuser && \
    chmod +x /app/start.sh && \
    chown -R appuser:appgroup /app
USER appuser

EXPOSE 8080

CMD ["/app/start.sh"]
