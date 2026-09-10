#!/usr/bin/env bash
# Reset ONLY a dedicated synthetic database; never reuse the business database.
set -euo pipefail
if [[ "${1:-}" != "--reset-import-test-db" || -z "${2:-}" ]]; then
  echo "Usage: benchmark-import-jobs.sh --reset-import-test-db NEW_REPORT_DIRECTORY [--no-tracemalloc]" >&2
  exit 2
fi
extra_args=()
large_repeats=(1)
if [[ -n "${3:-}" ]]; then
  [[ "$3" == "--no-tracemalloc" && "$#" == "3" ]] || { echo "Unsupported arguments" >&2; exit 2; }
  extra_args=(--no-tracemalloc)
  large_repeats=(1 2 3)
fi
[[ ! -e "$2" ]] || { echo "Report directory already exists" >&2; exit 2; }
export ADMIN_DATABASE_URL="${DATABASE_URL:?}"
export TEST_DATABASE_URL="${DATABASE_URL%/*}/app_import_perf_test"
/app/.venv/bin/python scripts/prepare_test_database.py
export DATABASE_URL="$TEST_DATABASE_URL"
/app/.venv/bin/python -m alembic upgrade head
mkdir -p "$2"
for repeat in 1 2 3; do
  # Alternate ordering to reduce systematic warm-up bias.
  modes=(sync queued)
  [[ "$repeat" != "2" ]] || modes=(queued sync)
  for mode in "${modes[@]}"; do
    /app/.venv/bin/python scripts/benchmark_import.py --rows 10000 --mode "$mode" --output "$2/10000-${mode}-${repeat}.json" "${extra_args[@]}"
  done
done
for repeat in "${large_repeats[@]}"; do
  modes=(sync queued)
  [[ "$repeat" != "2" ]] || modes=(queued sync)
  for mode in "${modes[@]}"; do
    /app/.venv/bin/python scripts/benchmark_import.py --rows 100000 --mode "$mode" --output "$2/100000-${mode}-${repeat}.json" "${extra_args[@]}"
  done
done
