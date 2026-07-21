#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CLI="$ROOT/bin/baton"
TMP="$(mktemp -d /tmp/baton-handoff-cancel.XXXXXX)"
DB="$TMP/baton.sqlite3"

"$CLI" --db "$DB" init >/dev/null
"$CLI" --db "$DB" role permission-list sm | grep 'handoff.cancel' >/dev/null

ROOT_JOB="$("$CLI" --db "$DB" register \
  --title "Cancelable root" \
  --role backend \
  --objective "Exercise scoped handoff cancellation." \
  --exit-criteria "Only this dependency tree is cancelled." | awk '{print $1}')"
CHILD_JOB="$("$CLI" --db "$DB" register \
  --title "Blocked child" \
  --role frontend \
  --depends-on "$ROOT_JOB" \
  --objective "Wait for the cancelable root." \
  --exit-criteria "The root is complete." | awk '{print $1}')"
GRANDCHILD_JOB="$("$CLI" --db "$DB" register \
  --title "Blocked grandchild" \
  --role qa \
  --depends-on "$CHILD_JOB" \
  --objective "Wait for the blocked child." \
  --exit-criteria "The child is complete." | awk '{print $1}')"
UNRELATED_JOB="$("$CLI" --db "$DB" register \
  --title "Unrelated frontend work" \
  --role frontend \
  --objective "Remain ready when another dependency tree is cancelled." \
  --exit-criteria "Independent work is preserved." | awk '{print $1}')"

"$CLI" --db "$DB" claim "$ROOT_JOB" --role backend --claimed-by cancel-test >/dev/null
if "$CLI" --db "$DB" cancel "$ROOT_JOB" --role frontend --reason "Unauthorized." >/dev/null 2>&1; then
  echo "ERROR: role without handoff.cancel cancelled a job" >&2
  exit 1
fi

"$CLI" --db "$DB" cancel "$ROOT_JOB" --role sm --reason "No longer required." | grep 'dependents=2' >/dev/null
"$CLI" --db "$DB" status | grep '^cancelled: 3$' >/dev/null
"$CLI" --db "$DB" status | grep '^open: 1$' >/dev/null
"$CLI" --db "$DB" next --role frontend | grep "$UNRELATED_JOB" >/dev/null

"$CLI" --db "$DB" events "$ROOT_JOB" | awk -F '\t' '$2 == "cancelled" && $5 == "No longer required." { found=1 } END { exit !found }'
"$CLI" --db "$DB" events "$CHILD_JOB" | awk -F '\t' '$2 == "dependency_cancelled" { found=1 } END { exit !found }'
"$CLI" --db "$DB" events "$GRANDCHILD_JOB" | awk -F '\t' '$2 == "dependency_cancelled" { found=1 } END { exit !found }'

if "$CLI" --db "$DB" cancel "$ROOT_JOB" --role sm --reason "Duplicate." >/dev/null 2>&1; then
  echo "ERROR: already-cancelled handoff was cancelled again" >&2
  exit 1
fi

"$CLI" --db "$DB" claim "$UNRELATED_JOB" --role frontend --claimed-by cancel-test >/dev/null
"$CLI" --db "$DB" finish "$UNRELATED_JOB" --role frontend --evidence "Independent work completed." >/dev/null
if "$CLI" --db "$DB" cancel "$UNRELATED_JOB" --role sm --reason "Too late." >/dev/null 2>&1; then
  echo "ERROR: finished handoff was cancelled" >&2
  exit 1
fi

echo "OK handoff cancel root=$ROOT_JOB unrelated=$UNRELATED_JOB db=$DB"
