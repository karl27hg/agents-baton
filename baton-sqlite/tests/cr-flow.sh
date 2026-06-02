#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CLI="$ROOT/baton"
TMP="$(mktemp -d /tmp/baton-cr-flow.XXXXXX)"
DB="$TMP/baton.sqlite3"
CR_DIR="$TMP/change-requests"

"$CLI" --db "$DB" init >/dev/null
"$CLI" --db "$DB" role permission-list sm | grep "cr.review" >/dev/null
"$CLI" --db "$DB" role permission-add architecture cr.review >/dev/null
"$CLI" --db "$DB" role permission-add architecture cr.approve >/dev/null
"$CLI" --db "$DB" role permission-list architecture | grep "cr.approve" >/dev/null

if "$CLI" --db "$DB" cr wait-review --role frontend --timeout 1 --interval 1 >/dev/null 2>&1; then
  echo "ERROR: role without cr.review unexpectedly waited for CR review" >&2
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
