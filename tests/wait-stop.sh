#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CLI="$ROOT/bin/baton"
TMP="$(mktemp /tmp/baton-wait-stop.XXXXXX)"
DB="$TMP.sqlite3"
OUT="$TMP.wait.out"

"$CLI" --db "$DB" init >/dev/null

if "$CLI" --db "$DB" wait --role frontend --timeout 1 --interval 0 >/dev/null 2>&1; then
  echo "ERROR: expected wait --interval 0 to fail" >&2
  exit 1
fi

if "$CLI" --db "$DB" cr wait-review --role sm --timeout 1 --interval 0 >/dev/null 2>&1; then
  echo "ERROR: expected cr wait-review --interval 0 to fail" >&2
  exit 1
fi

set +e
"$CLI" --db "$DB" wait --role frontend --timeout 30 --interval 1 >"$OUT" 2>&1 &
WAIT_PID="$!"
sleep 2
"$CLI" --db "$DB" stop --role frontend --reason "test stop" >/dev/null
wait "$WAIT_PID"
WAIT_STATUS="$?"
set -e

if [[ "$WAIT_STATUS" -ne 3 ]]; then
  echo "ERROR: expected wait exit status 3, got $WAIT_STATUS" >&2
  cat "$OUT" >&2
  exit 1
fi

grep "Stopped waiting for role frontend" "$OUT" >/dev/null
"$CLI" --db "$DB" control status | grep "role:frontend" | grep "stopped" >/dev/null
"$CLI" --db "$DB" resume --role frontend >/dev/null
"$CLI" --db "$DB" control status | grep "role:frontend" | grep "running" >/dev/null

echo "OK wait stop db=$DB"
