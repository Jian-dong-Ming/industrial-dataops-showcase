#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" != "--allow-billed-requests" || -z "${2:-}" || -e "$2" ]]; then
  echo "Usage: bash scripts/test-ai-business-live.sh --allow-billed-requests NEW_REPORT.json" >&2
  exit 2
fi
: "${DATABASE_URL:?Use the backend container environment}"
export DATABASE_URL="${DATABASE_URL%/*}/app_test"
export AI_LIVE_ACCEPTANCE=1 AI_BUSINESS_REPORT="$2"
export AI_BUSINESS_CASE_IDS="${3:-}"
uv run pytest tests/api/routes/test_assistant_business.py -k test_live_business_workflows -q
