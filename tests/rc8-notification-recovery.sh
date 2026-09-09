#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CLI="$ROOT/bin/baton"
TMP="$(mktemp -d /tmp/baton-rc8-notify.XXXXXX)"
DB="$TMP/baton.sqlite3"

"$CLI" --db "$DB" init >/dev/null
"$CLI" --db "$DB" agent session-set \
  --role planning \
  --agent-id planner-main \
  --thread-id codex-thread-planner \
  --model gpt-6-astra >/dev/null
"$CLI" --db "$DB" agent session-set \
  --role backend \
  --agent-id backend-main \
  --thread-id codex-thread-backend \
  --model gpt-5.6-sol >/dev/null

DIRECT_JOB="$("$CLI" --db "$DB" register \
  --title "Direct CR implementation" \
  --role backend \
  --source-ref cr:CR-EXAMPLE \
  --objective "Verify direct ready-handoff notification lookup." \
  --exit-criteria "The registered backend agent is discoverable." | awk '{print $1}')"

"$CLI" --db "$DB" notify candidates "$DIRECT_JOB" \
  --role planning \
  --from-agent planner-main \
  | grep "$DIRECT_JOB.*candidate.*backend-main.*codex-thread-backend" >/dev/null

"$CLI" --db "$DB" shift end --role backend --reason "Outside delivery hours." >/dev/null
"$CLI" --db "$DB" notify candidates "$DIRECT_JOB" \
  --role planning \
  --from-agent planner-main \
  | grep "$DIRECT_JOB.*outside_shift" >/dev/null
if "$CLI" --db "$DB" notify record "$DIRECT_JOB" \
  --role planning \
  --from-agent planner-main \
  --to-agent backend-main \
  --status sent >/dev/null 2>&1; then
  echo "ERROR: successful notification was recorded outside the target shift" >&2
  exit 1
fi
"$CLI" --db "$DB" shift start --role backend --duration 1h >/dev/null

"$CLI" --db "$DB" notify record "$DIRECT_JOB" \
  --role planning \
  --from-agent planner-main \
  --to-agent backend-main \
  --status failed \
  --detail "Initial host attempt failed." \
  | grep 'delivery_attempt=1' >/dev/null
INITIAL_OUTPUT="$("$CLI" --db "$DB" notify record "$DIRECT_JOB" \
  --role planning \
  --from-agent planner-main \
  --to-agent backend-main \
  --status sent \
  --message-ref codex-initial \
  --detail "Host accepted the initial follow-up.")"
INITIAL_NOTIFICATION="$(sed -n 's/^notification=\([0-9][0-9]*\).*/\1/p' <<<"$INITIAL_OUTPUT")"
grep 'delivery_attempt=2' <<<"$INITIAL_OUTPUT" >/dev/null
test -n "$INITIAL_NOTIFICATION"

if "$CLI" --db "$DB" notify record "$DIRECT_JOB" \
  --role planning \
  --from-agent planner-main \
  --to-agent backend-main \
  --status sent >/dev/null 2>&1; then
  echo "ERROR: ordinary record bypassed initial delivery deduplication" >&2
  exit 1
fi
if "$CLI" --db "$DB" notify record "$DIRECT_JOB" \
  --role planning \
  --from-agent planner-main \
  --to-agent backend-main \
  --status failed \
  --detail "An ordinary record must not represent recovery." >/dev/null 2>&1; then
  echo "ERROR: ordinary failed record bypassed the controlled recovery path" >&2
  exit 1
fi
"$CLI" --db "$DB" notify status "$DIRECT_JOB" --stale-after 1d \
  | grep '^notification_state: host_accepted_unclaimed$' >/dev/null
"$CLI" --db "$DB" notify status "$DIRECT_JOB" --stale-after 1d \
  | grep '^latest_delivery_attempt: 2$' >/dev/null
if "$CLI" --db "$DB" notify retry "$DIRECT_JOB" \
  --notification "$INITIAL_NOTIFICATION" \
  --role planning \
  --from-agent planner-main \
  --status sent \
  --reason "Recipient has not claimed the handoff." \
  --stale-after 1d >/dev/null 2>&1; then
  echo "ERROR: recovery notification was accepted before the stale threshold" >&2
  exit 1
fi

sleep 2
"$CLI" --db "$DB" shift end --role backend --reason "Recovery is outside shift." >/dev/null
if "$CLI" --db "$DB" notify retry "$DIRECT_JOB" \
  --notification "$INITIAL_NOTIFICATION" \
  --role planning \
  --from-agent planner-main \
  --status sent \
  --reason "Recipient has not claimed the handoff." \
  --stale-after 1s >/dev/null 2>&1; then
  echo "ERROR: recovery notification was recorded outside the target shift" >&2
  exit 1
fi
"$CLI" --db "$DB" shift start --role backend --duration 1h >/dev/null

RECOVERY_OUTPUT="$("$CLI" --db "$DB" notify retry "$DIRECT_JOB" \
  --notification "$INITIAL_NOTIFICATION" \
  --role planning \
  --from-agent planner-main \
  --status sent \
  --reason "stale_unclaimed" \
  --stale-after 1s \
  --message-ref codex-recovery \
  --detail "Host accepted the one recovery follow-up.")"
grep 'delivery_attempt=3' <<<"$RECOVERY_OUTPUT" >/dev/null
grep "retry_of=$INITIAL_NOTIFICATION" <<<"$RECOVERY_OUTPUT" >/dev/null
if "$CLI" --db "$DB" notify retry "$DIRECT_JOB" \
  --notification "$INITIAL_NOTIFICATION" \
  --role planning \
  --from-agent planner-main \
  --status sent \
  --reason "second recovery" \
  --stale-after 1s >/dev/null 2>&1; then
  echo "ERROR: more than one recovery notification was accepted" >&2
  exit 1
fi
"$CLI" --db "$DB" notify status "$DIRECT_JOB" --stale-after 1d \
  | grep '^latest_delivery_attempt: 3$' >/dev/null
"$CLI" --db "$DB" notify status "$DIRECT_JOB" --stale-after 1d \
  | grep '^recovery_delivery_attempts: 1$' >/dev/null
"$CLI" --db "$DB" notify list --job "$DIRECT_JOB" \
  | grep "delivery_attempt=3.*retry_of=$INITIAL_NOTIFICATION.*reason=stale_unclaimed" >/dev/null

FAILED_RECOVERY_JOB="$("$CLI" --db "$DB" register \
  --title "Failed recovery is final" \
  --role backend \
  --objective "Verify that a failed recovery consumes the recovery allowance." \
  --exit-criteria "A second recovery attempt is rejected." | awk '{print $1}')"
FAILED_RECOVERY_INITIAL_OUTPUT="$("$CLI" --db "$DB" notify record "$FAILED_RECOVERY_JOB" \
  --role planning \
  --from-agent planner-main \
  --to-agent backend-main \
  --status sent \
  --message-ref failed-recovery-initial)"
FAILED_RECOVERY_INITIAL="$(sed -n 's/^notification=\([0-9][0-9]*\).*/\1/p' \
  <<<"$FAILED_RECOVERY_INITIAL_OUTPUT")"
test -n "$FAILED_RECOVERY_INITIAL"
sleep 2
"$CLI" --db "$DB" notify retry "$FAILED_RECOVERY_JOB" \
  --notification "$FAILED_RECOVERY_INITIAL" \
  --role planning \
  --from-agent planner-main \
  --status failed \
  --reason "host_rejected_recovery" \
  --stale-after 1s \
  --detail "Recovery host call failed." \
  | grep 'delivery_attempt=2' >/dev/null
if "$CLI" --db "$DB" notify retry "$FAILED_RECOVERY_JOB" \
  --notification "$FAILED_RECOVERY_INITIAL" \
  --role planning \
  --from-agent planner-main \
  --status sent \
  --reason "second recovery after failure" \
  --stale-after 1s >/dev/null 2>&1; then
  echo "ERROR: failed recovery did not consume the recovery allowance" >&2
  exit 1
fi
"$CLI" --db "$DB" notify status "$FAILED_RECOVERY_JOB" --stale-after 1d \
  | grep '^latest_delivery_state: failed$' >/dev/null
"$CLI" --db "$DB" notify status "$FAILED_RECOVERY_JOB" --stale-after 1d \
  | grep '^recovery_delivery_attempts: 1$' >/dev/null

NO_RECORD_JOB="$("$CLI" --db "$DB" register \
  --title "No notification audit context" \
  --role backend \
  --objective "Expose factual no-record context." \
  --exit-criteria "Open, claimed, and finished contexts are distinct." | awk '{print $1}')"
"$CLI" --db "$DB" notify status "$NO_RECORD_JOB" \
  | grep '^notification_state: not_notified$' >/dev/null
"$CLI" --db "$DB" notify status "$NO_RECORD_JOB" \
  | grep '^notification_context: open_without_recorded_notification$' >/dev/null
"$CLI" --db "$DB" claim "$NO_RECORD_JOB" --role backend --claimed-by backend-main >/dev/null
"$CLI" --db "$DB" notify status "$NO_RECORD_JOB" \
  | grep '^notification_context: claimed_without_recorded_notification$' >/dev/null
"$CLI" --db "$DB" finish "$NO_RECORD_JOB" --role backend \
  --evidence "No notification was required." >/dev/null
"$CLI" --db "$DB" notify status "$NO_RECORD_JOB" \
  | grep '^notification_context: finished_without_recorded_notification$' >/dev/null

LEGACY_DB="$TMP/schema13.sqlite3"
"$CLI" --db "$LEGACY_DB" init >/dev/null
"$CLI" --db "$LEGACY_DB" agent session-set \
  --role planning --agent-id legacy-planner \
  --thread-id legacy-planner-thread --model gpt-6-astra >/dev/null
"$CLI" --db "$LEGACY_DB" agent session-set \
  --role backend --agent-id legacy-backend \
  --thread-id legacy-backend-thread --model gpt-5.6-sol >/dev/null
LEGACY_JOB="$("$CLI" --db "$LEGACY_DB" register \
  --title "Legacy notification rows" --role backend \
  --objective "Preserve schema 13 notification history." \
  --exit-criteria "Delivery attempts are backfilled in ID order." | awk '{print $1}')"
"$CLI" --db "$LEGACY_DB" notify record "$LEGACY_JOB" \
  --role planning --from-agent legacy-planner --to-agent legacy-backend \
  --status failed --detail "Legacy failure." >/dev/null
"$CLI" --db "$LEGACY_DB" notify record "$LEGACY_JOB" \
  --role planning --from-agent legacy-planner --to-agent legacy-backend \
  --status sent --message-ref legacy-success >/dev/null

python3 - "$LEGACY_DB" <<'PY'
import sqlite3
import sys

with sqlite3.connect(sys.argv[1]) as con:
    con.executescript(
        """
        drop index idx_handoff_notifications_delivery_attempt;
        drop index idx_handoff_notifications_initial_sent;
        drop index idx_handoff_notifications_recovery;
        alter table handoff_notifications rename to handoff_notifications_v14;
        create table handoff_notifications (
          id integer primary key autoincrement,
          job_id text not null references handoff_jobs(job_id) on delete cascade,
          sender_session_id text not null references agent_sessions(session_id),
          recipient_session_id text not null references agent_sessions(session_id),
          sender_agent_id text not null,
          sender_model text not null,
          recipient_agent_id text not null,
          recipient_thread_id text not null,
          recipient_model text not null,
          transport text not null,
          delivery_status text not null check (delivery_status in ('sent', 'failed')),
          message_ref text,
          detail text,
          created_at text not null,
          attempt integer not null default 1
        );
        insert into handoff_notifications(
          id, job_id, sender_session_id, recipient_session_id,
          sender_agent_id, sender_model, recipient_agent_id,
          recipient_thread_id, recipient_model, transport,
          delivery_status, message_ref, detail, created_at, attempt
        )
        select id, job_id, sender_session_id, recipient_session_id,
               sender_agent_id, sender_model, recipient_agent_id,
               recipient_thread_id, recipient_model, transport,
               delivery_status, message_ref, detail, created_at, attempt
        from handoff_notifications_v14;
        drop table handoff_notifications_v14;
        create index idx_handoff_notifications_job
          on handoff_notifications(job_id, id);
        create unique index idx_handoff_notifications_sent_attempt
          on handoff_notifications(job_id, attempt) where delivery_status = 'sent';
        create index idx_handoff_notifications_recipient
          on handoff_notifications(recipient_agent_id, created_at);
        delete from schema_migrations where version = 14;
        """
    )
PY

MIGRATION_OUTPUT="$("$CLI" --db "$LEGACY_DB" migrate)"
grep 'schema=13->14 applied=14:notification_recovery' <<<"$MIGRATION_OUTPUT" >/dev/null
grep "Agent action: read 'baton guide show upgrade' and 'baton guide show changelog', then review project AGENTS.md" \
  <<<"$MIGRATION_OUTPUT" >/dev/null
python3 - "$LEGACY_DB" "$LEGACY_JOB" <<'PY'
import sqlite3
import sys

with sqlite3.connect(sys.argv[1]) as con:
    rows = con.execute(
        "select delivery_attempt, retry_of_notification_id, recovery_reason "
        "from handoff_notifications where job_id = ? order by id",
        (sys.argv[2],),
    ).fetchall()
    quick_check = con.execute("pragma quick_check").fetchone()[0]
if rows != [(1, None, None), (2, None, None)]:
    raise SystemExit(f"unexpected migrated notification rows: {rows}")
if quick_check != "ok":
    raise SystemExit("migrated database failed quick_check")
PY
"$CLI" --db "$LEGACY_DB" migrate --check | grep 'schema=14' >/dev/null

echo "OK RC8 notification recovery db=$DB legacy=$LEGACY_DB"
