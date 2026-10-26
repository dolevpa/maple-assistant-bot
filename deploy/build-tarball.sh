#!/usr/bin/env bash
# Build the deployment tarball: /workspace/maplestory-bot-deploy.tar.gz
# Excludes .venv, .env, caches, bot.log, bot.pid, .git. Never reads secrets.
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_NAME="$(basename "${PROJECT_DIR}")"
OUT="${1:-$(dirname "${PROJECT_DIR}")/maplestory-bot-deploy.tar.gz}"

tar -czf "${OUT}" \
  --exclude="${PROJECT_NAME}/.venv" \
  --exclude="${PROJECT_NAME}/.env" \
  --exclude="${PROJECT_NAME}/.git" \
  --exclude="${PROJECT_NAME}/cache" \
  --exclude="__pycache__" \
  --exclude="*.pyc" \
  --exclude="${PROJECT_NAME}/bot.log" \
  --exclude="${PROJECT_NAME}/bot.pid" \
  -C "$(dirname "${PROJECT_DIR}")" "${PROJECT_NAME}"
chmod 600 "${OUT}"
echo "Built ${OUT} ($(du -h "${OUT}" | cut -f1))"
