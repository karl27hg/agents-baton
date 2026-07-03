#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CLI="$ROOT/bin/baton"
TMP="$(mktemp -d /tmp/baton-cr-flow.XXXXXX)"
DB="$TMP/baton.sqlite3"
CR_DIR="$TMP/change-requests"

"$CLI" --db "$DB" init >/dev/null
"$CLI" --db "$DB" role permission-list sm | grep "cr.review" >/dev/null
"$CLI" --db "$DB" role permission-list sm | grep "cr.admin" >/dev/null
"$CLI" --db "$DB" role permission-add architecture cr.review >/dev/null
"$CLI" --db "$DB" role permission-add architecture cr.approve >/dev/null
"$CLI" --db "$DB" role permission-list architecture | grep "cr.approve" >/dev/null
"$CLI" --db "$DB" role add product --display-name "Product" >/dev/null
"$CLI" --db "$DB" role permission-add product cr.approve >/dev/null

if "$CLI" --db "$DB" cr wait-review --role frontend --timeout 1 --interval 1 >/dev/null 2>&1; then
  echo "ERROR: role without cr.review unexpectedly waited for CR review" >&2
  exit 1
fi

if "$CLI" --db "$DB" cr create \
  --title "Self review should fail" \
  --author-role planning \
  --reviewer-role planning \
  --dir "$CR_DIR" >/dev/null 2>&1; then
  echo "ERROR: self-review CR creation unexpectedly succeeded" >&2
  exit 1
fi

CR_LINE="$("$CLI" --db "$DB" cr create \
  --title "Upload policy" \
  --author-role planning \
  --reviewer-role sm \
  --dir "$CR_DIR")"
CR_ID="$(awk '{print $1}' <<<"$CR_LINE")"
CR_FILE="$(awk '{print $3}' <<<"$CR_LINE")"

grep "status: draft" "$CR_FILE" >/dev/null
"$CLI" --db "$DB" cr submit "$CR_ID" --role planning >/dev/null
"$CLI" --db "$DB" cr wait-review --role sm --timeout 1 --interval 1 | grep "$CR_ID" >/dev/null

REVISION_LINE="$("$CLI" --db "$DB" cr request-revision "$CR_ID" \
  --role sm \
  --reason "Acceptance criteria is unclear.")"
REVISION_JOB="$(awk '{print $3}' <<<"$REVISION_LINE")"
grep "status: revision_requested" "$CR_FILE" >/dev/null
grep "active_revision_job_id: $REVISION_JOB" "$CR_FILE" >/dev/null

if "$CLI" --db "$DB" cr request-revision "$CR_ID" --role sm --reason "duplicate" >/dev/null 2>&1; then
  echo "ERROR: duplicate revision request unexpectedly succeeded" >&2
  exit 1
fi

"$CLI" --db "$DB" next --role planning | grep "$REVISION_JOB" >/dev/null
"$CLI" --db "$DB" claim "$REVISION_JOB" --role planning --claimed-by planning-main >/dev/null
printf "\n- Clarified acceptance criteria.\n" >>"$CR_FILE"
"$CLI" --db "$DB" cr resubmit "$CR_ID" --role planning --evidence "Acceptance criteria clarified." >/dev/null
grep "status: submitted" "$CR_FILE" >/dev/null
if grep "active_revision_job_id: $REVISION_JOB" "$CR_FILE" >/dev/null; then
  echo "ERROR: active revision job was not cleared on resubmit" >&2
  exit 1
fi
"$CLI" --db "$DB" finish "$REVISION_JOB" --role planning --evidence "CR resubmitted." >/dev/null

"$CLI" --db "$DB" cr approve "$CR_ID" --role sm --evidence "Ready for implementation." >/dev/null
if "$CLI" --db "$DB" cr mark-implemented "$CR_ID" --role sm --evidence "No implementation handoff." >/dev/null 2>&1; then
  echo "ERROR: CR without implementation handoff was unexpectedly marked implemented" >&2
  exit 1
fi
"$CLI" --db "$DB" role permission-add architecture cr.assign_implementation >/dev/null
if "$CLI" --db "$DB" cr create-handoff "$CR_ID" \
  --by-role architecture \
  --role frontend \
  --title "Unauthorized implementation assignment" \
  --objective "Verify non-reviewer roles cannot assign implementation." \
  --exit-criteria "This command should fail." >/dev/null 2>&1; then
  echo "ERROR: non-reviewer role unexpectedly created implementation handoff" >&2
  exit 1
fi
IMPLEMENT_LINE="$("$CLI" --db "$DB" cr create-handoff "$CR_ID" \
  --by-role sm \
  --role frontend \
  --title "Implement upload policy UI" \
  --objective "Implement the approved upload policy UI." \
  --exit-criteria "UI behavior matches the approved CR.")"
IMPLEMENT_JOB="$(awk '{print $2}' <<<"$IMPLEMENT_LINE")"
"$CLI" --db "$DB" claim "$IMPLEMENT_JOB" --role frontend --claimed-by frontend-main >/dev/null
if "$CLI" --db "$DB" cr mark-implemented "$CR_ID" --role sm --evidence "Implementation still active." >/dev/null 2>&1; then
  echo "ERROR: CR with unfinished implementation handoff was unexpectedly marked implemented" >&2
  exit 1
fi
"$CLI" --db "$DB" finish "$IMPLEMENT_JOB" --role frontend --evidence "Implementation complete." >/dev/null
"$CLI" --db "$DB" cr mark-implemented "$CR_ID" --role sm --evidence "Implementation handoff finished." >/dev/null
"$CLI" --db "$DB" cr events "$CR_ID" | grep implemented >/dev/null
grep "status: implemented" "$CR_FILE" >/dev/null

ARCH_CR_LINE="$("$CLI" --db "$DB" cr create \
  --title "Architecture reviewed change" \
  --author-role planning \
  --reviewer-role architecture \
  --dir "$CR_DIR")"
ARCH_CR_ID="$(awk '{print $1}' <<<"$ARCH_CR_LINE")"
"$CLI" --db "$DB" cr submit "$ARCH_CR_ID" --role planning >/dev/null
"$CLI" --db "$DB" cr approve "$ARCH_CR_ID" --role architecture --evidence "Architecture approved." >/dev/null

PRODUCT_CR_LINE="$("$CLI" --db "$DB" cr create \
  --title "Product reviewed change" \
  --author-role planning \
  --reviewer-role product \
  --dir "$CR_DIR")"
PRODUCT_CR_ID="$(awk '{print $1}' <<<"$PRODUCT_CR_LINE")"
"$CLI" --db "$DB" cr submit "$PRODUCT_CR_ID" --role planning >/dev/null
if "$CLI" --db "$DB" cr approve "$PRODUCT_CR_ID" --role product --evidence "Product approved." >/dev/null 2>&1; then
  echo "ERROR: role with cr.approve but without cr.review unexpectedly approved CR" >&2
  exit 1
fi

STUCK_LINE="$("$CLI" --db "$DB" cr create \
  --title "Legacy self-review CR" \
  --author-role planning \
  --reviewer-role sm \
  --dir "$CR_DIR")"
STUCK_ID="$(awk '{print $1}' <<<"$STUCK_LINE")"
"$CLI" --db "$DB" cr submit "$STUCK_ID" --role planning >/dev/null
python3 - "$DB" "$STUCK_ID" <<'PY'
import sqlite3
import sys

db, cr_id = sys.argv[1], sys.argv[2]
with sqlite3.connect(db) as con:
    con.execute("update change_requests set reviewer_role = author_role where cr_id = ?", (cr_id,))
PY
if "$CLI" --db "$DB" cr approve "$STUCK_ID" --role planning --evidence "self approval" >/dev/null 2>&1; then
  echo "ERROR: legacy self-review CR was unexpectedly approved" >&2
  exit 1
fi
if "$CLI" --db "$DB" cr reassign-reviewer "$STUCK_ID" --role architecture --reviewer-role sm --reason "No admin permission" >/dev/null 2>&1; then
  echo "ERROR: non-admin role unexpectedly reassigned reviewer" >&2
  exit 1
fi
"$CLI" --db "$DB" cr reassign-reviewer "$STUCK_ID" --role sm --reviewer-role architecture --reason "Fix legacy self-review" >/dev/null
"$CLI" --db "$DB" cr approve "$STUCK_ID" --role architecture --evidence "Architecture approved reassigned CR." >/dev/null
"$CLI" --db "$DB" cr events "$STUCK_ID" | grep "reviewer_reassigned" >/dev/null

CANCEL_LINE="$("$CLI" --db "$DB" cr create \
  --title "Legacy self-review CR to cancel" \
  --author-role planning \
  --reviewer-role sm \
  --dir "$CR_DIR")"
CANCEL_ID="$(awk '{print $1}' <<<"$CANCEL_LINE")"
"$CLI" --db "$DB" cr submit "$CANCEL_ID" --role planning >/dev/null
python3 - "$DB" "$CANCEL_ID" <<'PY'
import sqlite3
import sys

db, cr_id = sys.argv[1], sys.argv[2]
with sqlite3.connect(db) as con:
    con.execute("update change_requests set reviewer_role = author_role where cr_id = ?", (cr_id,))
PY
"$CLI" --db "$DB" cr cancel "$CANCEL_ID" --role sm --reason "Superseded by replacement CR" >/dev/null
"$CLI" --db "$DB" cr status "$CANCEL_ID" | grep "cancelled" >/dev/null
"$CLI" --db "$DB" cr events "$CANCEL_ID" | grep "cancelled" >/dev/null

WAIT_DB="$TMP/wait.sqlite3"
WAIT_OUT="$TMP/wait-review.out"
"$CLI" --db "$WAIT_DB" init >/dev/null
set +e
"$CLI" --db "$WAIT_DB" cr wait-review --role sm --timeout 30 --interval 1 >"$WAIT_OUT" 2>&1 &
WAIT_PID="$!"
sleep 2
"$CLI" --db "$WAIT_DB" stop --role sm --reason "test stop" >/dev/null
wait "$WAIT_PID"
WAIT_STATUS="$?"
set -e

if [[ "$WAIT_STATUS" -ne 3 ]]; then
  echo "ERROR: expected wait-review exit status 3, got $WAIT_STATUS" >&2
  cat "$WAIT_OUT" >&2
  exit 1
fi
grep "Stopped waiting for CR review role sm" "$WAIT_OUT" >/dev/null
"$CLI" --db "$WAIT_DB" resume --role sm >/dev/null
"$CLI" --db "$WAIT_DB" control status | grep "role:sm" | grep "running" >/dev/null

echo "OK cr flow cr=$CR_ID revision_job=$REVISION_JOB implementation_job=$IMPLEMENT_JOB db=$DB"
