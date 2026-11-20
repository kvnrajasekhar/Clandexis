#!/usr/bin/env bash
# Runs the ledger integration tests against the real Firestore emulator.
# Requires: Java 21+ on PATH and firebase-tools (`firebase` on PATH, or set FIREBASE_BIN).
set -euo pipefail
cd "$(dirname "$0")/.."
FIREBASE_BIN="${FIREBASE_BIN:-firebase}"
PY="${PYTHON:-.venv/bin/python}"
"$FIREBASE_BIN" emulators:exec --only firestore --project demo-clandexis \
  "PYTHONPATH=. $PY -m pytest tests -v ${*:-}"
