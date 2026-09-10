#!/usr/bin/env bash
set -euo pipefail

# The backend serves this bundle, so use same-origin API calls. Do not accidentally
# bake frontend/.env's localhost development URL into a 127.0.0.1 deployment.
cd "$(dirname "$0")/.."
VITE_API_URL= bun run --filter frontend build
docker compose cp backend/app/frontend/. backend:/app/backend/app/frontend/
