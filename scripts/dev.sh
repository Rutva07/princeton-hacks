#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
(cd frontend && npm run build)
trap 'kill 0 2>/dev/null || true' EXIT
(
  cd backend
  ../.venv/bin/python -m uvicorn app.main:app --reload --port 8000
) &
(
  cd backend
  ../.venv/bin/python -m app.worker
) &
(
  cd frontend
  npm run preview -- --host 127.0.0.1 --port 5173 --strictPort
) &
wait
