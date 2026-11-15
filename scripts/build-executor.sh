#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
command -v docker >/dev/null || { echo 'Docker Desktop is required.'; exit 1; }
docker info >/dev/null || { echo 'Start Docker Desktop, then rerun this command.'; exit 1; }
docker build --pull -t researchforge-executor:py312 backend/executor
echo 'Built isolated experiment image: researchforge-executor:py312'
