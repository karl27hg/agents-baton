#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CLI="$ROOT/baton"
TMP="$(mktemp /tmp/baton-shift.XXXXXX)"
DB="$TMP.sqlite3"

"$CLI" --db "$DB" init >/dev/null
"$CLI" --db "$DB" shift start --role frontend --duration 1s | grep "role:frontend" | grep "active" >/dev/null

JOB1="$("$CLI" --db "$DB" register \
  --title "Finish after shift expiry" \
  --role frontend \
  --objective "Verify finish is allowed after shift expiry." \
  --exit-criteria "The in-progress job can be finished." | awk '{print $1}')"
"$CLI" --db "$DB" claim "$JOB1" --role frontend --claimed-by shift-a >/dev/null
sleep 2
"$CLI" --db "$DB" finish "$JOB1" --role frontend --evidence "Finished after shift expiry." >/dev/null

JOB2="$("$CLI" --db "$DB" register \
  --title "Claim blocked after shift expiry" \
  --role frontend \
  --objective "Verify new claims are blocked after shift expiry." \
  --exit-criteria "Claim fails until the shift is extended." | awk '{print $1}')"
if "$CLI" --db "$DB" claim "$JOB2" --role frontend --claimed-by shift-b >/dev/null 2>&1; then
  echo "ERROR: claim after shift expiry unexpectedly succeeded" >&2
  exit 1
fi
"$CLI" --db "$DB" shift status --role frontend | grep "role:frontend" | grep "expired" >/dev/null

set +e
"$CLI" --db "$DB" wait --role frontend --timeout 1 --interval 1 >/tmp/baton-shift-wait.out 2>&1
WAIT_STATUS="$?"
set -e
if [[ "$WAIT_STATUS" -ne 3 ]]; then
  echo "ERROR: expected wait exit status 3 after shift expiry, got $WAIT_STATUS" >&2
  cat /tmp/baton-shift-wait.out >&2
  exit 1
fi

"$CLI" --db "$DB" shift extend --role frontend --duration 1m | grep "active" >/dev/null
"$CLI" --db "$DB" claim "$JOB2" --role frontend --claimed-by shift-b >/dev/null

echo "OK shift db=$DB"
