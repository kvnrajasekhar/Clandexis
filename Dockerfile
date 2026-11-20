# ==============================================================================
# Multi-stage Dockerfile for Clandexis Invisible ERP (FastAPI + React Dashboard)
# Target Deployment: Google Cloud Run (asia-south1)
# ==============================================================================

# --- Stage 1: Build React Vite Dashboard ---
FROM node:20-slim AS frontend-builder
WORKDIR /app/dashboard

COPY dashboard/package*.json ./
RUN npm ci

COPY dashboard/ ./
RUN npm run build

# --- Stage 2: Python Backend Runtime ---
FROM python:3.11-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    PORT=8080

WORKDIR /app

# Install system dependencies (for audio, certs, and font rendering in PDFs)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install Python requirements
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend application source
COPY . ./

# Ensure Python packages are real directories (handles zip source extraction flattening symlinks)
RUN if [ ! -d /app/webhook_service ]; then rm -rf /app/webhook_service && cp -r /app/webhook-service /app/webhook_service; fi && \
    if [ ! -d /app/agent_orchestrator ]; then rm -rf /app/agent_orchestrator && cp -r /app/agent-orchestrator /app/agent_orchestrator; fi

# Copy built frontend assets into dashboard/dist for static serving
COPY --from=frontend-builder /app/dashboard/dist ./dashboard/dist

# Non-root security user
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:${PORT}/health || exit 1

CMD ["sh", "-c", "uvicorn webhook_service.main:app --host 0.0.0.0 --port ${PORT}"]
