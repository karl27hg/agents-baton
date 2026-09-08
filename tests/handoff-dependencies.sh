#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CLI="$ROOT/bin/baton"
TMP="$(mktemp -d /tmp/baton-handoff-dependencies.XXXXXX)"
DB="$TMP/wait.sqlite3"
WAIT_OUT="$TMP/wait.out"
BLOCKED_OUT="$TMP/blocked-wait.out"

"$CLI" --db "$DB" init >/dev/null
UPSTREAM="$("$CLI" --db "$DB" register \
  --title "Upstream work" \
  --role backend \
  --objective "Complete the upstream work." \
  --exit-criteria "Upstream evidence is recorded." | awk '{print $1}')"
DEPENDENT="$("$CLI" --db "$DB" register \
  --title "Dependent work" \
  --role frontend \
  --depends-on "$UPSTREAM" \
  --objective "Run after upstream work." \
  --exit-criteria "Dependent evidence is recorded." | awk '{print $1}')"

if "$CLI" --db "$DB" next --role frontend >/dev/null 2>&1; then
  echo "ERROR: blocked handoff was unexpectedly ready" >&2
  exit 1
fi

set +e
"$CLI" --db "$DB" wait --role frontend --timeout 1 --interval 1 >"$BLOCKED_OUT" 2>&1
WAIT_STATUS="$?"
set -e
if [[ "$WAIT_STATUS" -ne 2 ]]; then
  echo "ERROR: expected blocked wait to time out with status 2, got $WAIT_STATUS" >&2
  exit 1
fi
grep "Timed out waiting for role frontend" "$BLOCKED_OUT" >/dev/null
if grep -E "Summary:|No ready jobs" "$BLOCKED_OUT" >/dev/null; then
  echo "ERROR: blocked wait emitted periodic polling output" >&2
  cat "$BLOCKED_OUT" >&2
  exit 1
fi

"$CLI" --db "$DB" wait --role frontend --timeout 10 --interval 1 >"$WAIT_OUT" 2>&1 &
WAIT_PID="$!"
sleep 1
"$CLI" --db "$DB" claim "$UPSTREAM" --role backend --claimed-by dependency-test >/dev/null
"$CLI" --db "$DB" finish "$UPSTREAM" --role backend --evidence "Upstream completed." >/dev/null
wait "$WAIT_PID"
grep "$DEPENDENT" "$WAIT_OUT" >/dev/null
PROMOTED_COUNT="$("$CLI" --db "$DB" events "$DEPENDENT" | awk -F '\t' '$2 == "promoted" { count++ } END { print count + 0 }')"
if [[ "$PROMOTED_COUNT" -ne 1 ]]; then
  echo "ERROR: expected one promoted event, got $PROMOTED_COUNT" >&2
  exit 1
fi

MULTI_DB="$TMP/multiple.sqlite3"
"$CLI" --db "$MULTI_DB" init >/dev/null
FIRST="$("$CLI" --db "$MULTI_DB" register \
  --title "First prerequisite" \
  --role backend \
  --objective "Complete the first prerequisite." \
  --exit-criteria "The first result is recorded." | awk '{print $1}')"
SECOND="$("$CLI" --db "$MULTI_DB" register \
  --title "Second prerequisite" \
  --role qa \
  --objective "Complete the second prerequisite." \
  --exit-criteria "The second result is recorded." | awk '{print $1}')"
JOINED="$("$CLI" --db "$MULTI_DB" register \
  --title "Joined successor" \
  --role frontend \
  --depends-on "$FIRST" \
  --depends-on "$SECOND" \
  --objective "Start only after both prerequisites." \
  --exit-criteria "Both finished results are consumed." | awk '{print $1}')"
"$CLI" --db "$MULTI_DB" claim "$FIRST" --role backend --claimed-by backend-main >/dev/null
"$CLI" --db "$MULTI_DB" finish "$FIRST" --role backend --evidence "First prerequisite complete." >/dev/null
"$CLI" --db "$MULTI_DB" handoff show "$JOINED" | grep 'status: blocked' >/dev/null
"$CLI" --db "$MULTI_DB" claim "$SECOND" --role qa --claimed-by qa-main >/dev/null
"$CLI" --db "$MULTI_DB" finish "$SECOND" --role qa --evidence "Second prerequisite complete." >/dev/null
"$CLI" --db "$MULTI_DB" handoff show "$JOINED" | grep 'status: open' >/dev/null
MULTI_PROMOTED_COUNT="$("$CLI" --db "$MULTI_DB" events "$JOINED" | awk -F '\t' '$2 == "promoted" { count++ } END { print count + 0 }')"
if [[ "$MULTI_PROMOTED_COUNT" -ne 1 ]]; then
  echo "ERROR: expected one multi-dependency promoted event, got $MULTI_PROMOTED_COUNT" >&2
  exit 1
fi

CANCEL_DB="$TMP/cancel.sqlite3"
CR_DIR="$TMP/change-requests"
"$CLI" --db "$CANCEL_DB" init >/dev/null
CR_ID="$("$CLI" --db "$CANCEL_DB" cr create \
  --title "Cancelled dependency cascade" \
  --author-role planning \
  --reviewer-role sm \
  --dir "$CR_DIR" | awk '{print $1}')"
"$CLI" --db "$CANCEL_DB" cr submit "$CR_ID" --role planning >/dev/null
REVISION_JOB="$("$CLI" --db "$CANCEL_DB" cr request-revision "$CR_ID" \
  --role sm \
  --reason "Revision is no longer needed." | awk '{print $3}')"
CHILD_JOB="$("$CLI" --db "$CANCEL_DB" register \
  --title "Wait for revision" \
  --role frontend \
  --depends-on "$REVISION_JOB" \
  --objective "Run after the revision." \
  --exit-criteria "Revision-dependent work is complete." | awk '{print $1}')"
GRANDCHILD_JOB="$("$CLI" --db "$CANCEL_DB" register \
  --title "Wait for dependent work" \
  --role qa \
  --depends-on "$CHILD_JOB" \
  --objective "Run after dependent work." \
  --exit-criteria "Dependency chain is complete." | awk '{print $1}')"

"$CLI" --db "$CANCEL_DB" cr cancel "$CR_ID" --role sm --reason "Superseded." >/dev/null
"$CLI" --db "$CANCEL_DB" status | grep '^cancelled: 3$' >/dev/null
"$CLI" --db "$CANCEL_DB" events "$REVISION_JOB" | awk -F '\t' '$2 == "cancelled" { found=1 } END { exit !found }'
"$CLI" --db "$CANCEL_DB" events "$CHILD_JOB" | awk -F '\t' '$2 == "dependency_cancelled" { found=1 } END { exit !found }'
"$CLI" --db "$CANCEL_DB" events "$GRANDCHILD_JOB" | awk -F '\t' '$2 == "dependency_cancelled" { found=1 } END { exit !found }'

LATE_STATUS="$("$CLI" --db "$CANCEL_DB" register \
  --title "Late cancelled dependency" \
  --role qa \
  --depends-on "$REVISION_JOB" \
  --objective "Never run after a cancelled dependency." \
  --exit-criteria "The handoff remains cancelled." | awk '{print $2}')"
if [[ "$LATE_STATUS" != "cancelled" ]]; then
  echo "ERROR: expected a new handoff with a cancelled dependency to be cancelled" >&2
  exit 1
fi
"$CLI" --db "$CANCEL_DB" status | grep '^cancelled: 4$' >/dev/null

LEGACY_DB="$TMP/legacy.sqlite3"
"$CLI" --db "$LEGACY_DB" init >/dev/null
LEGACY_UPSTREAM="$("$CLI" --db "$LEGACY_DB" register \
  --title "Legacy cancelled upstream" \
  --role backend \
  --objective "Simulate an older database." \
  --exit-criteria "Legacy state is reconciled." | awk '{print $1}')"
LEGACY_CHILD="$("$CLI" --db "$LEGACY_DB" register \
  --title "Legacy blocked child" \
  --role frontend \
  --depends-on "$LEGACY_UPSTREAM" \
  --objective "Be reconciled from blocked to cancelled." \
  --exit-criteria "Legacy state is reconciled." | awk '{print $1}')"
python3 - "$LEGACY_DB" "$LEGACY_UPSTREAM" <<'PY'
import sqlite3
import sys

db, job_id = sys.argv[1], sys.argv[2]
with sqlite3.connect(db) as con:
    con.execute("update handoff_jobs set status = 'cancelled' where job_id = ?", (job_id,))
PY
"$CLI" --db "$LEGACY_DB" promote-ready >/dev/null
"$CLI" --db "$LEGACY_DB" events "$LEGACY_CHILD" | awk -F '\t' '$2 == "dependency_cancelled" { found=1 } END { exit !found }'
"$CLI" --db "$LEGACY_DB" status | grep '^cancelled: 2$' >/dev/null

INPUT_DB="$TMP/input-validation.sqlite3"
INPUT_ERROR="$TMP/input-validation.err"
"$CLI" --db "$INPUT_DB" init >/dev/null
INPUT_UPSTREAM="$("$CLI" --db "$INPUT_DB" register \
  --title "Input validation upstream" \
  --role backend \
  --objective "Provide a dependency identifier." \
  --exit-criteria "Duplicate input is rejected." | awk '{print $1}')"
if "$CLI" --db "$INPUT_DB" register \
  --title "Duplicate dependency" \
  --role frontend \
  --depends-on "$INPUT_UPSTREAM" \
  --depends-on "$INPUT_UPSTREAM" \
  --objective "This handoff must not be created." \
  --exit-criteria "The CLI returns a stable user error." >"$INPUT_ERROR" 2>&1; then
  echo "ERROR: duplicate handoff dependency unexpectedly succeeded" >&2
  exit 1
fi
grep "ERROR: duplicate handoff dependency: $INPUT_UPSTREAM" "$INPUT_ERROR" >/dev/null
if grep 'Traceback' "$INPUT_ERROR" >/dev/null; then
  echo "ERROR: duplicate handoff dependency exposed a stack trace" >&2
  exit 1
fi
"$CLI" --db "$INPUT_DB" handoff list --format json \
  | grep -c '"job_id"' | grep '^1$' >/dev/null

python3 - "$INPUT_DB" "$INPUT_UPSTREAM" <<'PY'
import sqlite3
import sys

with sqlite3.connect(sys.argv[1]) as con:
    con.execute("update handoff_jobs set status = 'failed' where job_id = ?", (sys.argv[2],))
PY
FAILED_WARNING="$TMP/failed-dependency.err"
FAILED_DEPENDENT="$("$CLI" --db "$INPUT_DB" register \
  --title "Failed dependency warning" \
  --role frontend \
  --depends-on "$INPUT_UPSTREAM" \
  --objective "Remain blocked behind failed work." \
  --exit-criteria "The failed predecessor is resolved." 2>"$FAILED_WARNING" | awk '{print $1}')"
grep "WARNING: dependency $INPUT_UPSTREAM is failed" "$FAILED_WARNING" >/dev/null
"$CLI" --db "$INPUT_DB" handoff show "$FAILED_DEPENDENT" | grep 'status: blocked' >/dev/null
"$CLI" --db "$INPUT_DB" events "$FAILED_DEPENDENT" \
  | grep "Failed dependencies: $INPUT_UPSTREAM" >/dev/null

echo "OK handoff dependencies db=$DB cancel_db=$CANCEL_DB"
