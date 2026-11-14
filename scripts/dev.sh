#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
trap 'kill 0 2>/dev/null || true' EXIT
(
  cd backend
  ../.venv/bin/python -m uvicorn app.main:app --reload --port 8000
) &
(
  cd frontend
  npm run dev -- --host 127.0.0.1
) &
wait
