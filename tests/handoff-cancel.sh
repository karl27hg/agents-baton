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

"$CLI" --db "$DB" cancel "$ROOT_JOB" --role sm --reason "No longer required." | grep "Cancellation requested $ROOT_JOB" >/dev/null
"$CLI" --db "$DB" status | grep '^cancel_requested: 1$' >/dev/null
"$CLI" --db "$DB" status | grep '^blocked: 2$' >/dev/null
if "$CLI" --db "$DB" finish "$ROOT_JOB" --role backend --evidence "Stale completion." >/dev/null 2>&1; then
  echo "ERROR: cancel-requested handoff was finished" >&2
  exit 1
fi
if "$CLI" --db "$DB" cancel-ack "$ROOT_JOB" --role backend --claimed-by another-agent \
  --evidence "Wrong claimant." >/dev/null 2>&1; then
  echo "ERROR: a different agent acknowledged cancellation" >&2
  exit 1
fi
"$CLI" --db "$DB" cancel-ack "$ROOT_JOB" --role backend --claimed-by cancel-test \
  --evidence "Stopped before committing and retained the worktree for inspection." | grep 'dependents=2' >/dev/null
"$CLI" --db "$DB" status | grep '^cancelled: 3$' >/dev/null
"$CLI" --db "$DB" status | grep '^open: 1$' >/dev/null
"$CLI" --db "$DB" next --role frontend | grep "$UNRELATED_JOB" >/dev/null

"$CLI" --db "$DB" events "$ROOT_JOB" | awk -F '\t' '$2 == "cancellation_requested" && $5 == "No longer required." { found=1 } END { exit !found }'
"$CLI" --db "$DB" events "$ROOT_JOB" | awk -F '\t' '$2 == "cancellation_acknowledged" && $5 ~ /Stopped before committing/ { found=1 } END { exit !found }'
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

FORCE_JOB="$("$CLI" --db "$DB" register \
  --title "Disconnected claimant" \
  --role backend \
  --objective "Exercise emergency cancellation." \
  --exit-criteria "An authorized force cancellation is audited." | awk '{print $1}')"
"$CLI" --db "$DB" claim "$FORCE_JOB" --role backend --claimed-by disconnected-agent >/dev/null
"$CLI" --db "$DB" cancel "$FORCE_JOB" --role sm --reason "Claimant disconnected." --force | grep "Cancelled $FORCE_JOB" >/dev/null
"$CLI" --db "$DB" events "$FORCE_JOB" | awk -F '\t' '$2 == "cancellation_forced" { found=1 } END { exit !found }'

echo "OK handoff cancel root=$ROOT_JOB unrelated=$UNRELATED_JOB db=$DB"
