#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CLI="$ROOT/bin/baton"
TMP="$(mktemp -d /tmp/baton-workstream-routing.XXXXXX)"
DB="$TMP/baton.sqlite3"
CR_DIR="$TMP/change-requests"

"$CLI" --db "$DB" init >/dev/null
"$CLI" --db "$DB" role add integration --display-name "Integration" >/dev/null
"$CLI" --db "$DB" role permission-add integration cr.review >/dev/null
"$CLI" --db "$DB" role permission-add integration cr.approve >/dev/null

"$CLI" --db "$DB" agent workstream-add api-contract \
  --role integration --agent-id integration-api >/dev/null
"$CLI" --db "$DB" agent workstream-add ui-regression \
  --role integration --agent-id integration-ui >/dev/null
"$CLI" --db "$DB" agent workstream-add release-decision \
  --role integration --agent-id integration-final >/dev/null
"$CLI" --db "$DB" agent workstream-list --role integration \
  | grep $'integration-api\tintegration\tapi-contract' >/dev/null
"$CLI" --db "$DB" agent session-set --role planning --agent-id planning-sender \
  --thread-id planning-thread --model planner-model >/dev/null
"$CLI" --db "$DB" agent session-set --role integration --agent-id integration-api \
  --thread-id integration-api-thread --model review-model >/dev/null
"$CLI" --db "$DB" agent session-set --role integration --agent-id integration-ui \
  --thread-id integration-ui-thread --model review-model >/dev/null

API_JOB="$("$CLI" --db "$DB" register \
  --title "Verify API contract" \
  --role integration \
  --workstream api-contract \
  --objective "Verify the API contract independently." \
  --exit-criteria "API contract evidence is recorded." | awk '{print $1}')"
UI_JOB="$("$CLI" --db "$DB" register \
  --title "Verify UI regression" \
  --role integration \
  --workstream ui-regression \
  --objective "Verify UI regressions independently." \
  --exit-criteria "UI regression evidence is recorded." | awk '{print $1}')"
FINAL_JOB="$("$CLI" --db "$DB" register \
  --title "Make final integration decision" \
  --role integration \
  --workstream release-decision \
  --depends-on "$API_JOB" \
  --depends-on "$UI_JOB" \
  --objective "Integrate the independent verification evidence once." \
  --exit-criteria "One owner records the final integration decision." | awk '{print $1}')"

"$CLI" --db "$DB" next --role integration --agent-id integration-api \
  | grep "$API_JOB" >/dev/null
"$CLI" --db "$DB" next --role integration --agent-id integration-ui \
  | grep "$UI_JOB" >/dev/null
if "$CLI" --db "$DB" claim "$API_JOB" \
  --role integration --claimed-by integration-ui >/dev/null 2>&1; then
  echo "ERROR: an agent claimed a handoff outside its workstream" >&2
  exit 1
fi
"$CLI" --db "$DB" claim "$API_JOB" \
  --role integration --claimed-by integration-api >/dev/null
if "$CLI" --db "$DB" claim "$UI_JOB" \
  --role integration --claimed-by integration-api >/dev/null 2>&1; then
  echo "ERROR: one agent claimed two active units of work" >&2
  exit 1
fi
"$CLI" --db "$DB" claim "$UI_JOB" \
  --role integration --claimed-by integration-ui >/dev/null
"$CLI" --db "$DB" finish "$API_JOB" \
  --role integration --evidence "API contract passed." >/dev/null
"$CLI" --db "$DB" handoff show "$FINAL_JOB" | grep 'status: blocked' >/dev/null
"$CLI" --db "$DB" finish "$UI_JOB" \
  --role integration --evidence "UI regression passed." >/dev/null
"$CLI" --db "$DB" handoff show "$FINAL_JOB" | grep 'status: open' >/dev/null
"$CLI" --db "$DB" claim "$FINAL_JOB" \
  --role integration --claimed-by integration-final >/dev/null
"$CLI" --db "$DB" finish "$FINAL_JOB" \
  --role integration --evidence "All required evidence integrated once." >/dev/null

NOTIFY_SOURCE="$("$CLI" --db "$DB" register \
  --title "Prepare routed verification" \
  --role planning \
  --objective "Prepare workstream notification input." \
  --exit-criteria "The routed successor is ready." | awk '{print $1}')"
NOTIFY_TARGET="$("$CLI" --db "$DB" register \
  --title "Review routed verification" \
  --role integration \
  --workstream api-contract \
  --depends-on "$NOTIFY_SOURCE" \
  --objective "Verify notification routing." \
  --exit-criteria "Only a matching idle specialist is selected." | awk '{print $1}')"
"$CLI" --db "$DB" claim "$NOTIFY_SOURCE" \
  --role planning --claimed-by planning-sender >/dev/null
"$CLI" --db "$DB" finish "$NOTIFY_SOURCE" \
  --role planning --evidence "Routed successor is ready." >/dev/null
NOTIFY_OUTPUT="$("$CLI" --db "$DB" notify targets "$NOTIFY_SOURCE" \
  --role planning --from-agent planning-sender)"
grep "$NOTIFY_TARGET.*api-contract.*candidate.*integration-api" <<<"$NOTIFY_OUTPUT" >/dev/null
if grep 'integration-ui' <<<"$NOTIFY_OUTPUT" >/dev/null; then
  echo "ERROR: notification selected an unrelated workstream" >&2
  exit 1
fi

CR_ID="$("$CLI" --db "$DB" cr create \
  --title "Integration review routing" \
  --author-role planning \
  --reviewer-role integration \
  --reviewer-workstream api-contract \
  --dir "$CR_DIR" | awk '{print $1}')"
"$CLI" --db "$DB" cr submit "$CR_ID" --role planning >/dev/null
if "$CLI" --db "$DB" cr wait-review --role integration \
  --agent-id integration-ui --timeout 1 --interval 1 >/dev/null 2>&1; then
  echo "ERROR: unrelated workstream observed a routed CR review" >&2
  exit 1
fi
"$CLI" --db "$DB" cr wait-review --role integration \
  --agent-id integration-api --timeout 1 --interval 1 | grep "$CR_ID" >/dev/null
if "$CLI" --db "$DB" cr claim-review "$CR_ID" \
  --role integration --claimed-by integration-ui >/dev/null 2>&1; then
  echo "ERROR: unrelated workstream claimed a routed CR review" >&2
  exit 1
fi
"$CLI" --db "$DB" cr claim-review "$CR_ID" \
  --role integration --claimed-by integration-api >/dev/null
"$CLI" --db "$DB" notify targets "$NOTIFY_SOURCE" \
  --role planning --from-agent planning-sender \
  | grep "$NOTIFY_TARGET.*no_active_peer_session" >/dev/null
if "$CLI" --db "$DB" cr approve "$CR_ID" \
  --role integration --claimed-by integration-ui >/dev/null 2>&1; then
  echo "ERROR: a non-claimant reviewed a claimed CR" >&2
  exit 1
fi

CAPACITY_JOB="$("$CLI" --db "$DB" register \
  --title "Second API verification" \
  --role integration \
  --workstream api-contract \
  --objective "Verify capacity while a review is active." \
  --exit-criteria "The review claimant cannot claim this handoff concurrently." | awk '{print $1}')"
if "$CLI" --db "$DB" claim "$CAPACITY_JOB" \
  --role integration --claimed-by integration-api >/dev/null 2>&1; then
  echo "ERROR: active CR reviewer claimed another handoff" >&2
  exit 1
fi
"$CLI" --db "$DB" cr release-review "$CR_ID" \
  --role integration --claimed-by integration-api --reason "Yield for urgent verification." >/dev/null
"$CLI" --db "$DB" claim "$CAPACITY_JOB" \
  --role integration --claimed-by integration-api >/dev/null
"$CLI" --db "$DB" finish "$CAPACITY_JOB" \
  --role integration --evidence "Urgent verification complete." >/dev/null
"$CLI" --db "$DB" cr claim-review "$CR_ID" \
  --role integration --claimed-by integration-api >/dev/null
"$CLI" --db "$DB" cr approve "$CR_ID" \
  --role integration --claimed-by integration-api --evidence "Review complete." >/dev/null

"$CLI" --db "$DB" agent workstream-add api-contract \
  --role integration --agent-id integration-api-2 >/dev/null
RACE_CR="$("$CLI" --db "$DB" cr create \
  --title "Concurrent review claim" \
  --author-role planning \
  --reviewer-role integration \
  --reviewer-workstream api-contract \
  --dir "$CR_DIR" | awk '{print $1}')"
"$CLI" --db "$DB" cr submit "$RACE_CR" --role planning >/dev/null
set +e
"$CLI" --db "$DB" cr claim-review "$RACE_CR" \
  --role integration --claimed-by integration-api >"$TMP/review-a.out" 2>&1 &
RACE_A_PID="$!"
"$CLI" --db "$DB" cr claim-review "$RACE_CR" \
  --role integration --claimed-by integration-api-2 >"$TMP/review-b.out" 2>&1 &
RACE_B_PID="$!"
wait "$RACE_A_PID"
RACE_A_STATUS="$?"
wait "$RACE_B_PID"
RACE_B_STATUS="$?"
set -e
RACE_SUCCESS=0
[[ "$RACE_A_STATUS" -eq 0 ]] && RACE_SUCCESS=$((RACE_SUCCESS + 1))
[[ "$RACE_B_STATUS" -eq 0 ]] && RACE_SUCCESS=$((RACE_SUCCESS + 1))
if [[ "$RACE_SUCCESS" -ne 1 ]]; then
  echo "ERROR: expected exactly one successful CR review claim" >&2
  exit 1
fi
"$CLI" --db "$DB" handoff show "$API_JOB" | grep 'workstream: api-contract' >/dev/null
python3 - "$DB" <<'PY'
import sqlite3
import sys

with sqlite3.connect(sys.argv[1]) as con:
    con.execute("delete from schema_migrations where version = 11")
PY
if "$CLI" --db "$DB" migrate >/dev/null 2>&1; then
  echo "ERROR: migration accepted an active claimed CR review" >&2
  exit 1
fi

echo "OK workstream routing api=$API_JOB ui=$UI_JOB final=$FINAL_JOB cr=$CR_ID race_cr=$RACE_CR db=$DB"
