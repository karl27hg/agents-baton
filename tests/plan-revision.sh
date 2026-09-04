#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CLI="$ROOT/bin/baton"
TMP="$(mktemp -d /tmp/baton-plan-revision.XXXXXX)"
DB="$TMP/baton.sqlite3"
CR_DIR="$TMP/change-requests"

"$CLI" --db "$DB" init >/dev/null

create_approved_cr() {
  local title="$1"
  local cr_id
  cr_id="$("$CLI" --db "$DB" cr create \
    --title "$title" \
    --author-role backend \
    --reviewer-role sm \
    --dir "$CR_DIR" | awk '{print $1}')"
  "$CLI" --db "$DB" cr submit "$cr_id" --role backend >/dev/null
  "$CLI" --db "$DB" cr approve "$cr_id" --role sm --evidence "Approved for test." >/dev/null
  printf '%s\n' "$cr_id"
}

OLD_CR="$(create_approved_cr "Original design")"
NEW_CR="$(create_approved_cr "Replacement design")"

OPEN_JOB="$("$CLI" --db "$DB" cr create-handoff "$OLD_CR" \
  --by-role sm \
  --role frontend \
  --title "Queued old implementation" \
  --objective "Implement the original design." \
  --exit-criteria "Original design is implemented." | awk '{print $2}')"
BLOCKED_JOB="$("$CLI" --db "$DB" register \
  --title "Old downstream verification" \
  --role qa \
  --depends-on "$OPEN_JOB" \
  --objective "Verify the original implementation." \
  --exit-criteria "Original implementation is verified." | awk '{print $1}')"
ACTIVE_JOB="$("$CLI" --db "$DB" cr create-handoff "$OLD_CR" \
  --by-role sm \
  --role backend \
  --title "Active old implementation" \
  --objective "Implement another part of the original design." \
  --exit-criteria "Original backend design is implemented." | awk '{print $2}')"
FINISHED_JOB="$("$CLI" --db "$DB" cr create-handoff "$OLD_CR" \
  --by-role sm \
  --role architecture \
  --title "Finished old analysis" \
  --objective "Analyze the original design." \
  --exit-criteria "Analysis is recorded." | awk '{print $2}')"

"$CLI" --db "$DB" claim "$ACTIVE_JOB" --role backend --claimed-by backend-main >/dev/null
"$CLI" --db "$DB" claim "$FINISHED_JOB" --role architecture --claimed-by architecture-main >/dev/null
"$CLI" --db "$DB" finish "$FINISHED_JOB" --role architecture --evidence "Analysis retained." >/dev/null

"$CLI" --db "$DB" cr supersede "$OLD_CR" \
  --by "$NEW_CR" \
  --role sm \
  --reason "The approved design changed incompatibly." \
  | grep 'cancel_requested=1' >/dev/null

"$CLI" --db "$DB" cr status "$OLD_CR" | grep "^$OLD_CR[[:space:]]superseded" >/dev/null
"$CLI" --db "$DB" cr status "$OLD_CR" | grep "superseded_by_cr_id: $NEW_CR" >/dev/null
"$CLI" --db "$DB" cr status "$OLD_CR" | grep 'body_integrity: ok' >/dev/null
"$CLI" --db "$DB" cr status "$NEW_CR" | grep "^$NEW_CR[[:space:]]approved" >/dev/null
"$CLI" --db "$DB" handoff show "$OPEN_JOB" | grep 'status: cancelled' >/dev/null
"$CLI" --db "$DB" handoff show "$BLOCKED_JOB" | grep 'status: cancelled' >/dev/null
"$CLI" --db "$DB" handoff show "$ACTIVE_JOB" | grep 'status: cancel_requested' >/dev/null
"$CLI" --db "$DB" handoff show "$FINISHED_JOB" | grep 'status: finished' >/dev/null
if "$CLI" --db "$DB" finish "$ACTIVE_JOB" --role backend --evidence "Stale completion." >/dev/null 2>&1; then
  echo "ERROR: superseded implementation was finished" >&2
  exit 1
fi
if "$CLI" --db "$DB" cancel-withdraw "$ACTIVE_JOB" --role sm \
  --reason "Unsafe attempt to restore retired design work." >/dev/null 2>&1; then
  echo "ERROR: cancellation tied to a superseded CR was withdrawn" >&2
  exit 1
fi
"$CLI" --db "$DB" cancel-ack "$ACTIVE_JOB" \
  --role backend \
  --claimed-by backend-main \
  --evidence "Stopped before commit after the replacement design was approved." >/dev/null
"$CLI" --db "$DB" cr events "$OLD_CR" | grep 'superseded' >/dev/null
"$CLI" --db "$DB" cr events "$NEW_CR" | grep 'supersedes' >/dev/null

CANCEL_CR="$(create_approved_cr "Cancelled design")"
CANCEL_JOB="$("$CLI" --db "$DB" cr create-handoff "$CANCEL_CR" \
  --by-role sm \
  --role frontend \
  --title "Cancelled design implementation" \
  --objective "Implement work that will be cancelled." \
  --exit-criteria "Cancellation is propagated." | awk '{print $2}')"
"$CLI" --db "$DB" cr cancel "$CANCEL_CR" --role sm --reason "Design withdrawn." \
  | grep 'cancelled_jobs=1' >/dev/null
"$CLI" --db "$DB" handoff show "$CANCEL_JOB" | grep 'status: cancelled' >/dev/null

DIRECT_CR="$(create_approved_cr "Design replaced by planner authority")"
"$CLI" --db "$DB" cr supersede "$DIRECT_CR" \
  --by-source-ref "abc123:docs/approved-design.md" \
  --role sm \
  --reason "SM issued an authoritative replacement design." >/dev/null
"$CLI" --db "$DB" cr status "$DIRECT_CR" \
  | grep 'superseded_by_ref: abc123:docs/approved-design.md' >/dev/null

echo "OK plan revision old=$OLD_CR new=$NEW_CR active=$ACTIVE_JOB db=$DB"
