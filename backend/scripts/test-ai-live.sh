#!/usr/bin/env bash
set -euo pipefail

if [[ "${1:-}" != "--allow-billed-requests" || -z "${2:-}" || -e "$2" ]]; then
  echo "Usage: bash scripts/test-ai-live.sh --allow-billed-requests NEW_REPORT.xml" >&2
  exit 2
fi
: "${DATABASE_URL:?Use the backend container environment}"
export DATABASE_URL="${DATABASE_URL%/*}/app_test"
export AI_LIVE_ACCEPTANCE=1
uv run pytest tests/api/routes/test_assistant.py -k test_live_embedding_document_lifecycle_and_scope --junitxml="$2" -q
