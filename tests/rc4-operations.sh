#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CLI="$ROOT/bin/baton"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/baton-rc4-operations.XXXXXX")"
DB="$TMP/project/.baton/baton.sqlite3"
OLD_DB="$TMP/old-project/.baton/baton.sqlite3"
CR_DIR="$TMP/project/.baton/change-requests"
trap 'rm -rf "$TMP"' EXIT

"$CLI" --db "$DB" init >/dev/null

set +e
"$CLI" --db "$DB" upgrade preflight >"$TMP/preflight-open.out" 2>&1
PREFLIGHT_STATUS="$?"
set -e
test "$PREFLIGHT_STATUS" = 2
grep 'Upgrade preflight NOT READY' "$TMP/preflight-open.out" >/dev/null
grep 'global_stop: no' "$TMP/preflight-open.out" >/dev/null

"$CLI" --db "$DB" agent session-set --role planning --agent-id planner-main \
  --host codex --thread-id planner-thread --model planner-model >/dev/null
"$CLI" --db "$DB" agent session-set --role backend --agent-id backend-main \
  --host codex --thread-id backend-thread --model backend-model >/dev/null
"$CLI" --db "$DB" agent session-set --role sm --agent-id sm-reviewer \
  --host codex --thread-id sm-thread --model reviewer-model >/dev/null
"$CLI" --db "$DB" agent workstream-add api-contract \
  --role backend --agent-id backend-main >/dev/null

ROUTED_JOB="$("$CLI" --db "$DB" register \
  --title "Routed backend work" \
  --role backend \
  --workstream api-contract \
  --objective "Exercise eligibility diagnostics." \
  --exit-criteria "The matching backend profile can inspect the job." | awk '{print $1}')"

set +e
BATON_AGENT_ID=planner-main "$CLI" --db "$DB" next --role backend --explain \
  >"$TMP/planner-next.out" 2>&1
NEXT_STATUS="$?"
set -e
test "$NEXT_STATUS" = 1
grep 'active session role planning, but requested role is backend' "$TMP/planner-next.out" >/dev/null
grep "excluded_handoff: $ROUTED_JOB reason=missing_workstream_registration" \
  "$TMP/planner-next.out" >/dev/null
BATON_AGENT_ID=backend-main "$CLI" --db "$DB" next --role backend --explain \
  >"$TMP/backend-next.out" 2>&1
grep "$ROUTED_JOB" "$TMP/backend-next.out" >/dev/null

CR_ID="$("$CLI" --db "$DB" cr create \
  --title "Listable CR" \
  --author-role planning \
  --reviewer-role sm \
  --dir "$CR_DIR" | awk '{print $1}')"
"$CLI" --db "$DB" cr list --status draft --body-integrity editable \
  | grep "$CR_ID.*draft.*sm.*editable" >/dev/null
"$CLI" --db "$DB" cr submit "$CR_ID" --role planning >/dev/null
"$CLI" --db "$DB" cr claim-review "$CR_ID" \
  --role sm --claimed-by sm-reviewer >/dev/null
"$CLI" --db "$DB" cr list --status submitted --reviewer-role sm \
  --claimed-by sm-reviewer --body-integrity ok \
  | grep "$CR_ID.*submitted.*sm.*sm-reviewer.*ok" >/dev/null

SOURCE_JOB="$("$CLI" --db "$DB" register \
  --title "Prepare notified work" \
  --role planning \
  --objective "Prepare a direct successor." \
  --exit-criteria "The successor becomes ready." | awk '{print $1}')"
TARGET_JOB="$("$CLI" --db "$DB" register \
  --title "Receive notified work" \
  --role backend \
  --workstream api-contract \
  --depends-on "$SOURCE_JOB" \
  --objective "Exercise notification diagnostics." \
  --exit-criteria "Host acceptance and claim state are visible." | awk '{print $1}')"
"$CLI" --db "$DB" claim "$SOURCE_JOB" \
  --role planning --claimed-by planner-main >/dev/null
"$CLI" --db "$DB" finish "$SOURCE_JOB" \
  --role planning --evidence "Successor is ready." >/dev/null
"$CLI" --db "$DB" notify targets "$SOURCE_JOB" \
  --role planning --from-agent planner-main \
  | grep "$TARGET_JOB.*candidate.*backend-main" >/dev/null
"$CLI" --db "$DB" notify record "$TARGET_JOB" \
  --role planning --from-agent planner-main --to-agent backend-main \
  --status sent --message-ref host-message-1 --detail "Host accepted the message." \
  | grep 'host_accepted' >/dev/null
"$CLI" --db "$DB" notify status "$TARGET_JOB" --stale-after 1d \
  | grep 'notification_state: host_accepted_unclaimed' >/dev/null

python3 - "$DB" "$TARGET_JOB" <<'PY'
import sqlite3
import sys

with sqlite3.connect(sys.argv[1]) as con:
    con.execute(
        "update handoff_notifications set created_at = '2020-01-01 00:00:00.000000 UTC' "
        "where job_id = ? and delivery_status = 'sent'",
        (sys.argv[2],),
    )
PY
"$CLI" --db "$DB" notify status "$TARGET_JOB" --stale-after 1s \
  | grep 'notification_state: stale_unclaimed' >/dev/null
"$CLI" --db "$DB" notify list --job "$TARGET_JOB" \
  | grep 'host_accepted' >/dev/null
"$CLI" --db "$DB" claim "$TARGET_JOB" \
  --role backend --claimed-by backend-main >/dev/null
"$CLI" --db "$DB" notify status "$TARGET_JOB" \
  | grep 'notification_state: claimed_by_recipient' >/dev/null

"$CLI" --db "$DB" stop --all --reason "RC4 preflight test" >/dev/null
set +e
"$CLI" --db "$DB" upgrade preflight >"$TMP/preflight-blocked.out" 2>&1
BLOCKED_STATUS="$?"
set -e
test "$BLOCKED_STATUS" = 2
grep "handoff: $TARGET_JOB status=in_progress" "$TMP/preflight-blocked.out" >/dev/null
grep "cr_review: $CR_ID" "$TMP/preflight-blocked.out" >/dev/null

"$CLI" --db "$DB" finish "$TARGET_JOB" \
  --role backend --evidence "Notification state verified." >/dev/null
"$CLI" --db "$DB" cr approve "$CR_ID" \
  --role sm --claimed-by sm-reviewer --evidence "List output verified." >/dev/null
"$CLI" --db "$DB" upgrade preflight \
  | grep 'Upgrade preflight READY' >/dev/null

PYTHONPATH="$ROOT/src" python3 - "$OLD_DB" <<'PY'
import sys
from pathlib import Path

from agents_baton import cli

path = Path(sys.argv[1])
path.parent.mkdir(parents=True)
with cli.connect(str(path), create=True) as con:
    con.execute("PRAGMA foreign_keys = OFF")
    con.execute(
        "create table schema_migrations ("
        "version integer primary key, name text not null, applied_at text not null)"
    )
    for version, name, migration in cli.MIGRATIONS:
        if version > 11:
            break
        migration(con)
        con.execute(
            "insert into schema_migrations(version, name, applied_at) values (?, ?, ?)",
            (version, name, cli.utc_now()),
        )
    con.execute(
        "insert into handoff_controls(scope, stopped, reason, work_until, updated_at) "
        "values ('all', 1, 'RC4 old-schema preflight', null, ?)",
        (cli.utc_now(),),
    )
PY
"$CLI" --db "$OLD_DB" upgrade preflight \
  | grep 'schema_version: 11' >/dev/null
"$CLI" --db "$OLD_DB" migrate \
  | grep 'schema=11->14 applied=12:retry_and_replacement_tracking,13:completion_evidence,14:notification_recovery' >/dev/null
"$CLI" --db "$OLD_DB" migrate --check | grep 'schema=14' >/dev/null

echo "OK RC4 operations routed=$ROUTED_JOB notification=$TARGET_JOB cr=$CR_ID db=$DB"
