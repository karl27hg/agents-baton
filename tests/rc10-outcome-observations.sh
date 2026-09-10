#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CLI="$ROOT/bin/baton"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/baton-rc10.XXXXXX")"
DB="$TMP/baton.sqlite3"
LEGACY_DB="$TMP/schema14.sqlite3"
CR_DIR="$TMP/change-requests"
trap 'rm -rf "$TMP"' EXIT

create_cr() {
  "$CLI" --db "$DB" cr create \
    --title "$1" \
    --author-role backend \
    --reviewer-role planning \
    --dir "$CR_DIR" | awk '{print $1}'
}

"$CLI" --db "$DB" init >/dev/null
CR_ONE="$(create_cr "First blocking outcome")"
CR_TWO="$(create_cr "Second blocking outcome")"
CR_THREE="$(create_cr "Post-completion outcome")"

JOB="$("$CLI" --db "$DB" register \
  --title "Multiple blocking findings" \
  --role backend \
  --objective "Record every CR needed to resolve the blocking result." \
  --exit-criteria "All outcome CRs are queryable." | awk '{print $1}')"
"$CLI" --db "$DB" claim "$JOB" --role backend --claimed-by backend-main >/dev/null
"$CLI" --db "$DB" finish "$JOB" \
  --role backend \
  --evidence "Two independent blocking findings were recorded." \
  --outcome fail \
  --blocking \
  --outcome-cr "$CR_ONE" \
  --outcome-cr "$CR_TWO" >/dev/null

"$CLI" --db "$DB" handoff show "$JOB" --format json >"$TMP/handoff.json"
"$CLI" --db "$DB" handoff list --format json >"$TMP/handoffs.json"
python3 - "$TMP/handoff.json" "$TMP/handoffs.json" "$CR_ONE" "$CR_TWO" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    handoff = json.load(handle)
with open(sys.argv[2], encoding="utf-8") as handle:
    handoffs = json.load(handle)

expected = [sys.argv[3], sys.argv[4]]
if handoff["outcome_cr_id"] != expected[0]:
    raise SystemExit(f"legacy outcome_cr_id projection changed: {handoff}")
if [row["cr_id"] for row in handoff["outcome_crs"]] != expected:
    raise SystemExit(f"handoff show lost outcome CR order: {handoff}")
listed = next(row for row in handoffs if row["job_id"] == handoff["job_id"])
if [row["cr_id"] for row in listed["outcome_crs"]] != expected:
    raise SystemExit(f"handoff list lost outcome CRs: {listed}")
PY

if "$CLI" --db "$DB" handoff outcome-cr-link "$JOB" \
  --cr "$CR_THREE" --role backend --claimed-by backend-main \
  --reason "Worker must not amend the reviewed blocker set." >/dev/null 2>&1; then
  echo "ERROR: role without handoff.evidence_correct appended an outcome CR" >&2
  exit 1
fi
"$CLI" --db "$DB" handoff outcome-cr-link "$JOB" \
  --cr "$CR_THREE" --role planning --claimed-by planner-main \
  --reason "The later audit identified an independent blocker." >/dev/null
if "$CLI" --db "$DB" handoff outcome-cr-link "$JOB" \
  --cr "$CR_THREE" --role planning --claimed-by planner-main \
  --reason "Duplicate link." >/dev/null 2>&1; then
  echo "ERROR: duplicate post-completion outcome CR link was accepted" >&2
  exit 1
fi
"$CLI" --db "$DB" handoff show "$JOB" \
  | grep "outcome_crs:.*$CR_ONE.*$CR_TWO.*$CR_THREE" >/dev/null
"$CLI" --db "$DB" events "$JOB" | grep 'outcome_cr_linked' >/dev/null

python3 - "$DB" "$CR_ONE" "$CR_TWO" "$CR_THREE" <<'PY'
import sqlite3
import sys

with sqlite3.connect(sys.argv[1]) as con:
    con.execute("update change_requests set status = 'implemented' where cr_id = ?", (sys.argv[2],))
    con.execute("update change_requests set status = 'implemented' where cr_id = ?", (sys.argv[3],))
    con.execute("update change_requests set status = 'approved' where cr_id = ?", (sys.argv[4],))
PY
"$CLI" --db "$DB" handoff show "$JOB" | grep '^blocking_context: open_cr$' >/dev/null
"$CLI" --db "$DB" status | grep '^outcome.blocking_with_open_cr: 1$' >/dev/null
python3 - "$DB" "$CR_THREE" <<'PY'
import sqlite3
import sys

with sqlite3.connect(sys.argv[1]) as con:
    con.execute("update change_requests set status = 'implemented' where cr_id = ?", (sys.argv[2],))
PY
"$CLI" --db "$DB" handoff show "$JOB" | grep '^blocking_context: implemented_cr$' >/dev/null
"$CLI" --db "$DB" status | grep '^outcome.blocking_with_implemented_cr: 1$' >/dev/null
python3 - "$DB" "$CR_TWO" <<'PY'
import sqlite3
import sys

with sqlite3.connect(sys.argv[1]) as con:
    con.execute("update change_requests set status = 'rejected' where cr_id = ?", (sys.argv[2],))
PY
"$CLI" --db "$DB" handoff show "$JOB" \
  | grep '^blocking_context: terminal_unimplemented_cr$' >/dev/null
"$CLI" --db "$DB" status \
  | grep '^outcome.blocking_with_terminal_unimplemented_cr: 1$' >/dev/null

"$CLI" --db "$DB" agent session-set \
  --role planning --agent-id planner-main \
  --thread-id planner-thread --model planner-model >/dev/null
"$CLI" --db "$DB" agent session-set \
  --role backend --agent-id backend-notified \
  --thread-id backend-thread --model backend-model >/dev/null
NOTIFY_JOB="$("$CLI" --db "$DB" register \
  --title "Observed receiver failure" \
  --role backend \
  --objective "Audit a bounded post-delivery execution result." \
  --exit-criteria "Observation does not mutate workflow state." | awk '{print $1}')"
NOTIFICATION_OUTPUT="$("$CLI" --db "$DB" notify record "$NOTIFY_JOB" \
  --role planning --from-agent planner-main --to-agent backend-notified \
  --status sent --message-ref host-message)"
NOTIFICATION_ID="$(sed -n 's/^notification=\([0-9][0-9]*\).*/\1/p' <<<"$NOTIFICATION_OUTPUT")"
test -n "$NOTIFICATION_ID"

if "$CLI" --db "$DB" notify observe "$NOTIFY_JOB" \
  --notification "$NOTIFICATION_ID" --role backend --claimed-by backend-notified \
  --result execution_failed --reason-class policy_blocked >/dev/null 2>&1; then
  echo "ERROR: role without notification.observe recorded an observation" >&2
  exit 1
fi
"$CLI" --db "$DB" notify observe "$NOTIFY_JOB" \
  --notification "$NOTIFICATION_ID" --role planning --claimed-by planner-main \
  --result execution_failed --reason-class policy_blocked >/dev/null
if "$CLI" --db "$DB" notify observe "$NOTIFY_JOB" \
  --notification "$NOTIFICATION_ID" --role planning --claimed-by planner-main \
  --result execution_failed --reason-class timeout >/dev/null 2>&1; then
  echo "ERROR: a second observation was accepted for one notification" >&2
  exit 1
fi
"$CLI" --db "$DB" notify status "$NOTIFY_JOB" \
  | grep '^latest_observation_result: execution_failed$' >/dev/null
"$CLI" --db "$DB" notify status "$NOTIFY_JOB" \
  | grep '^latest_observation_reason_class: policy_blocked$' >/dev/null
"$CLI" --db "$DB" notify status "$NOTIFY_JOB" \
  | grep '^recovery_delivery_attempts: 0$' >/dev/null
"$CLI" --db "$DB" notify list --job "$NOTIFY_JOB" \
  | grep 'observation=execution_failed.*reason_class=policy_blocked' >/dev/null
"$CLI" --db "$DB" handoff show "$NOTIFY_JOB" | grep '^status: open$' >/dev/null

"$CLI" --db "$LEGACY_DB" init >/dev/null
LEGACY_CR="$("$CLI" --db "$LEGACY_DB" cr create \
  --title "Legacy outcome" --author-role backend --reviewer-role planning \
  --dir "$TMP/legacy-crs" | awk '{print $1}')"
LEGACY_JOB="$("$CLI" --db "$LEGACY_DB" register \
  --title "Legacy schema 14 outcome" --role backend \
  --objective "Preserve the legacy outcome link." \
  --exit-criteria "Migration backfills one relationship." | awk '{print $1}')"
"$CLI" --db "$LEGACY_DB" claim "$LEGACY_JOB" --role backend >/dev/null
"$CLI" --db "$LEGACY_DB" finish "$LEGACY_JOB" --role backend \
  --evidence "Legacy blocking result." --outcome fail --blocking \
  --outcome-cr "$LEGACY_CR" >/dev/null
python3 - "$LEGACY_DB" <<'PY'
import sqlite3
import sys

with sqlite3.connect(sys.argv[1]) as con:
    con.execute("drop table notification_observations")
    con.execute("drop table handoff_outcome_crs")
    con.execute("delete from role_permissions where permission = 'notification.observe'")
    con.execute("delete from schema_migrations where version = 15")
PY
"$CLI" --db "$LEGACY_DB" migrate \
  | grep 'schema=14->15 applied=15:outcome_links_and_notification_observations' >/dev/null
"$CLI" --db "$LEGACY_DB" handoff show "$LEGACY_JOB" \
  | grep "outcome_crs:.*$LEGACY_CR.*migration" >/dev/null
"$CLI" --db "$LEGACY_DB" role permission-list planning \
  | grep $'^planning\tnotification.observe$' >/dev/null
"$CLI" --db "$LEGACY_DB" migrate --check | grep 'schema=15' >/dev/null

echo "OK RC10 multi-CR outcomes, bounded observations, and schema-14 migration"
