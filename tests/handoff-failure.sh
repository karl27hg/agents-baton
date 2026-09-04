#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CLI="$ROOT/bin/baton"
TMP="$(mktemp -d /tmp/baton-handoff-failure.XXXXXX)"
PROJECT="$TMP/project"
mkdir -p "$PROJECT"
cd "$PROJECT"

"$CLI" init >/dev/null
DB="$PROJECT/.baton/baton.sqlite3"

UPSTREAM="$("$CLI" register \
  --title "Failing backend work" \
  --role backend \
  --objective "Exercise failure review." \
  --exit-criteria "The retry is completed." | awk '{print $1}')"
DEPENDENT="$("$CLI" register \
  --title "Blocked frontend work" \
  --role frontend \
  --depends-on "$UPSTREAM" \
  --objective "Wait for successful backend work." \
  --exit-criteria "The backend dependency is finished." | awk '{print $1}')"

"$CLI" claim "$UPSTREAM" --role backend --claimed-by failure-test >/dev/null
FAIL_OUTPUT="$("$CLI" fail "$UPSTREAM" \
  --role backend \
  --reason "The API contract is incomplete." \
  --evidence "Contract validation failed.")"
CR_ID="$(printf '%s\n' "$FAIL_OUTPUT" | sed -E 's/.* cr=([^ ]+).*/\1/')"

"$CLI" status | grep '^failed: 1$' >/dev/null
"$CLI" handoff show "$DEPENDENT" | grep '^status: blocked$' >/dev/null
"$CLI" handoff show "$UPSTREAM" --format json | grep "\"cr_id\": \"$CR_ID\"" >/dev/null
"$CLI" cr status "$CR_ID" | grep $'submitted\t' >/dev/null
grep 'The API contract is incomplete.' ".baton/change-requests/$CR_ID"-*.md >/dev/null
if "$CLI" next --role frontend >/dev/null 2>&1; then
  echo "ERROR: dependent handoff became ready after upstream failure" >&2
  exit 1
fi
"$CLI" promote-ready >/dev/null
"$CLI" handoff show "$DEPENDENT" | grep '^status: blocked$' >/dev/null
if "$CLI" retry "$UPSTREAM" --role planning --reason "Retry too early." >/dev/null 2>&1; then
  echo "ERROR: failed handoff retried before CR approval" >&2
  exit 1
fi

"$CLI" cr approve "$CR_ID" --role planning --evidence "Retry with a corrected contract." >/dev/null
"$CLI" retry "$UPSTREAM" --role planning --cr-id "$CR_ID" --reason "Use the corrected contract." >/dev/null
"$CLI" handoff show "$UPSTREAM" | grep '^status: open$' >/dev/null
"$CLI" claim "$UPSTREAM" --role backend --claimed-by failure-retry >/dev/null
"$CLI" finish "$UPSTREAM" --role backend --evidence "Corrected contract validated." >/dev/null
"$CLI" promote-ready | grep "$DEPENDENT" >/dev/null
"$CLI" cr mark-implemented "$CR_ID" --role planning --evidence "Retry completed successfully." >/dev/null

CANCEL_ROOT="$("$CLI" register \
  --title "Abandoned backend work" \
  --role backend \
  --objective "Exercise rejected failure cancellation." \
  --exit-criteria "The branch is cancelled." | awk '{print $1}')"
CANCEL_CHILD="$("$CLI" register \
  --title "Cancelled dependent" \
  --role qa \
  --depends-on "$CANCEL_ROOT" \
  --objective "Never run after abandonment." \
  --exit-criteria "The dependency branch is cancelled." | awk '{print $1}')"
"$CLI" claim "$CANCEL_ROOT" --role backend >/dev/null
CANCEL_CR="$("$CLI" fail "$CANCEL_ROOT" --role backend --reason "The approach is invalid." | sed -E 's/.* cr=([^ ]+).*/\1/')"
if "$CLI" cancel "$CANCEL_ROOT" --role planning --reason "Premature cancellation." >/dev/null 2>&1; then
  echo "ERROR: failed handoff was cancelled before CR decision" >&2
  exit 1
fi
"$CLI" cr reject "$CANCEL_CR" --role planning --reason "Do not retry this approach." >/dev/null
"$CLI" cancel "$CANCEL_ROOT" --role planning --reason "Failure review rejected retry." >/dev/null
"$CLI" handoff show "$CANCEL_ROOT" | grep '^status: cancelled$' >/dev/null
"$CLI" handoff show "$CANCEL_CHILD" | grep '^status: cancelled$' >/dev/null

ADMIN_ROOT="$("$CLI" register \
  --title "Administratively cancelled failure" \
  --role backend \
  --objective "Exercise failure CR cancellation." \
  --exit-criteria "Administrative cancellation closes the branch." | awk '{print $1}')"
ADMIN_CHILD="$("$CLI" register \
  --title "Administrative cancellation dependent" \
  --role qa \
  --depends-on "$ADMIN_ROOT" \
  --objective "Remain blocked until cancellation." \
  --exit-criteria "The dependency branch is cancelled." | awk '{print $1}')"
"$CLI" claim "$ADMIN_ROOT" --role backend >/dev/null
ADMIN_CR="$("$CLI" fail "$ADMIN_ROOT" --role backend --reason "Escalate to SM." | sed -E 's/.* cr=([^ ]+).*/\1/')"
"$CLI" cr cancel "$ADMIN_CR" --role sm --reason "Work is no longer required." >/dev/null
"$CLI" handoff show "$ADMIN_ROOT" | grep '^status: cancelled$' >/dev/null
"$CLI" handoff show "$ADMIN_CHILD" | grep '^status: cancelled$' >/dev/null

PLANNING_JOB="$("$CLI" register \
  --title "Planning self-review protection" \
  --role planning \
  --objective "Assign planning failures to SM." \
  --exit-criteria "Self-review is prevented." | awk '{print $1}')"
"$CLI" claim "$PLANNING_JOB" --role planning >/dev/null
"$CLI" fail "$PLANNING_JOB" --role planning --reason "Planning needs external review." | grep 'reviewer=sm' >/dev/null

UNAUTHORIZED="$("$CLI" register \
  --title "Invalid reviewer rollback" \
  --role backend \
  --objective "Reject an unauthorized failure reviewer." \
  --exit-criteria "The command rolls back." | awk '{print $1}')"
"$CLI" claim "$UNAUTHORIZED" --role backend >/dev/null
if "$CLI" fail "$UNAUTHORIZED" --role backend --reviewer-role frontend --reason "Must roll back." >/dev/null 2>&1; then
  echo "ERROR: unauthorized failure reviewer was accepted" >&2
  exit 1
fi
"$CLI" handoff show "$UNAUTHORIZED" | grep '^status: in_progress$' >/dev/null

if "$CLI" register \
  --actor-role frontend \
  --title "Unauthorized registration" \
  --role qa \
  --objective "Must not register." \
  --exit-criteria "Registration is rejected." >/dev/null 2>&1; then
  echo "ERROR: fresh project allowed registration without handoff.register" >&2
  exit 1
fi
"$CLI" role permission-add frontend handoff.register >/dev/null
"$CLI" register \
  --actor-role frontend \
  --title "Authorized registration" \
  --role qa \
  --objective "Register after an explicit grant." \
  --exit-criteria "Registration succeeds." >/dev/null

echo "OK handoff failure project=$PROJECT cr=$CR_ID"
