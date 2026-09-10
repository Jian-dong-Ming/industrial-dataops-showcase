#!/usr/bin/env bash
# Explicitly rebuild ONLY the dedicated synthetic performance database.
set -euo pipefail
if [[ "${1:-}" != "--reset-performance-test-db" || -z "${2:-}" ]]; then
  echo "Usage: benchmark-latest.sh --reset-performance-test-db NEW_REPORT_PATH" >&2
  exit 2
fi
[[ ! -e "$2" ]] || { echo "Report already exists" >&2; exit 2; }
export ADMIN_DATABASE_URL="${DATABASE_URL:?}"
export TEST_DATABASE_URL="${DATABASE_URL%/*}/app_perf_test"
python scripts/prepare_test_database.py
export DATABASE_URL="$TEST_DATABASE_URL"
alembic upgrade head
python scripts/benchmark_latest.py --output "$2"
