#!/usr/bin/env bash
set -euo pipefail

# Run after tests-start.sh has prepared the isolated test database.
# This script never recreates or clears a database.
: "${DATABASE_URL:?Use the backend container environment}"
export DATABASE_URL="${DATABASE_URL%/*}/app_test"
uv run python -m app.assistant.evaluate "$@"
