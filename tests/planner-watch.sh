#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CLI="$ROOT/bin/baton"
TMP="$(mktemp -d /tmp/baton-planner-watch.XXXXXX)"
DB="$TMP/baton.sqlite3"
CR_DIR="$TMP/change-requests"

"$CLI" --db "$DB" init >/dev/null

HANDOFF_ID="$("$CLI" --db "$DB" register \
  --title "Planner follow-up" \
  --role sm \
  --objective "Reconcile completed work." \
  --exit-criteria "The plan is reconciled." | awk '{print $1}')"
CR_ID="$("$CLI" --db "$DB" cr create \
  --title "Review before follow-up" \
  --author-role backend \
  --reviewer-role sm \
  --dir "$CR_DIR" | awk '{print $1}')"
"$CLI" --db "$DB" cr submit "$CR_ID" --role backend >/dev/null

"$CLI" --db "$DB" watch --role sm --timeout 1 --interval 1 \
  | grep "^cr_review[[:space:]]$CR_ID" >/dev/null
"$CLI" --db "$DB" cr approve "$CR_ID" --role sm --evidence "Reviewed first." >/dev/null
"$CLI" --db "$DB" watch --role sm --timeout 1 --interval 1 \
  | grep "^handoff[[:space:]]$HANDOFF_ID" >/dev/null

FRONTEND_JOB="$("$CLI" --db "$DB" register \
  --title "Frontend-only watch" \
  --role frontend \
  --objective "Verify a non-reviewer can use watch." \
  --exit-criteria "The handoff is returned." | awk '{print $1}')"
"$CLI" --db "$DB" watch --role frontend --timeout 1 --interval 1 \
  | grep "^handoff[[:space:]]$FRONTEND_JOB" >/dev/null

set +e
TIMEOUT_OUTPUT="$("$CLI" --db "$DB" watch --role qa --timeout 1 --interval 1 2>&1)"
TIMEOUT_STATUS=$?
set -e
if [[ "$TIMEOUT_STATUS" -ne 2 ]] || [[ "$TIMEOUT_OUTPUT" != *"Timed out watching role qa"* ]]; then
  echo "ERROR: watch timeout contract failed" >&2
  exit 1
fi

"$CLI" --db "$DB" stop --role qa --reason "End planner watch test." >/dev/null
set +e
STOP_OUTPUT="$("$CLI" --db "$DB" watch --role qa --timeout 1 --interval 1 2>&1)"
STOP_STATUS=$?
set -e
if [[ "$STOP_STATUS" -ne 3 ]] || [[ "$STOP_OUTPUT" != *"Stopped watching role qa"* ]]; then
  echo "ERROR: watch stop contract failed" >&2
  exit 1
fi

python3 - "$DB" <<'PY'
import sqlite3
import sys

with sqlite3.connect(sys.argv[1]) as con:
    count = con.execute("select count(*) from waiter_leases").fetchone()[0]
    if count != 0:
        raise SystemExit(f"waiter lease leaked after watch: {count}")
PY

echo "OK planner watch cr=$CR_ID handoff=$HANDOFF_ID db=$DB"
