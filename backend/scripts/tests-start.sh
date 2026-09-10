#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${DATABASE_URL:-}" && -f ../.env ]]; then
  set -a
  # shellcheck disable=SC1091
  source ../.env
  set +a
fi

: "${DATABASE_URL:?DATABASE_URL must be configured}"
ADMIN_DATABASE_URL="${DATABASE_URL}"
TEST_DATABASE_URL="${TEST_DATABASE_URL:-${DATABASE_URL%/*}/app_test}"
SECRET_KEY="${TEST_SECRET_KEY:-industrial-dataops-platform-test-secret-key-2026-only}"
export ADMIN_DATABASE_URL TEST_DATABASE_URL SECRET_KEY

echo "Preparing isolated test database"
uv run python scripts/prepare_test_database.py
export DATABASE_URL="${TEST_DATABASE_URL}"

echo "Applying migrations and running backend tests"
uv run alembic upgrade head
uv run python app/tests_pre_start.py

bash scripts/test.sh "$@"
