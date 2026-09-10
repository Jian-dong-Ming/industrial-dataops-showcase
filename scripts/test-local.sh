#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${PROJECT_ROOT}/backend"
export COVERAGE_HTML_DIR="${COVERAGE_HTML_DIR:-${PROJECT_ROOT}/.reports/coverage}"

# Uses the existing PostgreSQL service and recreates only TEST_DATABASE_URL.
# The backend entry point refuses database names without the _test suffix.
exec bash scripts/tests-start.sh "$@"
