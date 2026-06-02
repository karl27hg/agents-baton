#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CLI="$ROOT/bin/baton"
TMP="$(mktemp /tmp/baton-concurrent.XXXXXX)"
DB="$TMP.sqlite3"
OUT_A="$TMP.a.out"
OUT_B="$TMP.b.out"

"$CLI" --db "$DB" init >/dev/null

JOB="$("$CLI" --db "$DB" register \
  --title "Concurrent claim smoke" \
  --role frontend \
  --objective "Exercise concurrent claim behavior." \
  --exit-criteria "Exactly one claimant succeeds." | awk '{print $1}')"

set +e
"$CLI" --db "$DB" claim "$JOB" --role frontend --claimed-by claimant-a >"$OUT_A" 2>&1 &
PID_A="$!"
"$CLI" --db "$DB" claim "$JOB" --role frontend --claimed-by claimant-b >"$OUT_B" 2>&1 &
PID_B="$!"
wait "$PID_A"
STATUS_A="$?"
wait "$PID_B"
STATUS_B="$?"
set -e

success_count=0
if [[ "$STATUS_A" -eq 0 ]]; then
  success_count=$((success_count + 1))
fi
if [[ "$STATUS_B" -eq 0 ]]; then
  success_count=$((success_count + 1))
fi

if [[ "$success_count" -ne 1 ]]; then
  echo "ERROR: expected exactly one successful claim, got $success_count" >&2
  echo "claimant-a status=$STATUS_A output=$(cat "$OUT_A")" >&2
  echo "claimant-b status=$STATUS_B output=$(cat "$OUT_B")" >&2
  exit 1
fi

echo "OK concurrent claim job=$JOB success_count=$success_count db=$DB"
