#!/usr/bin/env bash
# Separate network, RAM database and E-drive imports. No business .env.ai keys.
set -euo pipefail
cd "$(dirname "$0")/.."
export BROWSER_TEST_DATA_DIR="${BROWSER_TEST_DATA_DIR:-/mnt/e/industrial-dataops-browser-test/imports}"
case "$BROWSER_TEST_DATA_DIR" in /mnt/e/*browser-test*/imports) ;; *) echo 'Use a dedicated browser-test imports directory on E:' >&2; exit 2;; esac
mkdir -p "$BROWSER_TEST_DATA_DIR"
compose=(docker compose -p industrial-dataops-browser-test --env-file /dev/null -f compose.browser-test.yml)
case "${1:-help}" in
  start)
    "${compose[@]}" build backend
    "${compose[@]}" up -d --wait db mailcatcher
    "${compose[@]}" run --rm backend bash scripts/prestart.sh
    "${compose[@]}" run --rm backend python -m app.opcua.demo_seed
    "${compose[@]}" up -d --wait backend import-worker opcua-simulator opcua-collector
    ;;
  test)
    shift
    "${compose[@]}" run --build --rm playwright bunx playwright test "$@"
    ;;
  stop)
    # Only this standalone project's disposable containers/network; never -v.
    "${compose[@]}" down
    ;;
  *) echo 'Usage: bash scripts/browser-test-local.sh {start|test [playwright options]|stop}';;
esac
