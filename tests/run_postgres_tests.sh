#!/usr/bin/env bash
# Requires an explicitly selected, isolated local database with models/postgres_schema.sql.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${DATABASE_URL:?Set DATABASE_URL to the isolated PostgreSQL test database}"
export DATABASE_BACKEND=postgres PYTHONPATH=.
.venv/bin/python -m pytest tests/postgres_application_checks.py -v "$@"
