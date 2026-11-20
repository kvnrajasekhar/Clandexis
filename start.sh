#!/bin/sh
# Cloud Run entrypoint — resolves the hyphenated webhook-service directory
exec uvicorn "webhook-service.main:app" \
  --host 0.0.0.0 \
  --port "${PORT:-8080}" \
  --workers 1 \
  --log-level info
