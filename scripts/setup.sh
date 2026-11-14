#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if command -v python3.12 >/dev/null 2>&1; then PY=python3.12
elif command -v python3.13 >/dev/null 2>&1; then PY=python3.13
else echo 'Python 3.12 or 3.13 required. On macOS: brew install python@3.12'; exit 1
fi
command -v npm >/dev/null || { echo 'Install Node.js 20+ first'; exit 1; }
"$PY" -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r backend/requirements.txt
(cd frontend && npm install)
if [[ ! -f .env ]];then cp .env.example .env;fi
echo 'Setup complete. Edit .env to enable live integrations, then run: bash scripts/dev.sh'
