#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CLI="$ROOT/bin/baton"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/baton-rc9-notify-list.XXXXXX")"
DB="$TMP/baton.sqlite3"
trap 'rm -rf "$TMP"' EXIT

list_ids() {
  awk -F '\t' '/^[0-9]+\t/ { ids = ids (ids ? " " : "") $1 } END { print ids }'
}

"$CLI" --db "$DB" init >/dev/null
"$CLI" --db "$DB" agent session-set \
  --role planning \
  --agent-id planner-main \
  --thread-id planner-thread \
  --model planner-model >/dev/null
"$CLI" --db "$DB" agent session-set \
  --role backend \
  --agent-id backend-main \
  --thread-id backend-thread \
  --model backend-model >/dev/null

FIRST_JOB="$("$CLI" --db "$DB" register \
  --title "Notification list base rows" \
  --role backend \
  --objective "Create ordinary notification audit rows." \
  --exit-criteria "Oldest-first compatibility can be verified." | awk '{print $1}')"
"$CLI" --db "$DB" notify record "$FIRST_JOB" \
  --role planning \
  --from-agent planner-main \
  --to-agent backend-main \
  --status failed \
  --detail "Initial host failure." >/dev/null
"$CLI" --db "$DB" notify record "$FIRST_JOB" \
  --role planning \
  --from-agent planner-main \
  --to-agent backend-main \
  --status sent \
  --message-ref first-success >/dev/null

RECOVERY_JOB="$("$CLI" --db "$DB" register \
  --title "Notification list recovery row" \
  --role backend \
  --objective "Create one recovery notification audit row." \
  --exit-criteria "Recovery-only filtering returns this row." | awk '{print $1}')"
RECOVERY_INITIAL_OUTPUT="$("$CLI" --db "$DB" notify record "$RECOVERY_JOB" \
  --role planning \
  --from-agent planner-main \
  --to-agent backend-main \
  --status sent \
  --message-ref recovery-initial)"
RECOVERY_INITIAL="$(sed -n 's/^notification=\([0-9][0-9]*\).*/\1/p' \
  <<<"$RECOVERY_INITIAL_OUTPUT")"
test "$RECOVERY_INITIAL" = "3"

python3 - "$DB" "$RECOVERY_INITIAL" <<'PY'
import sqlite3
import sys

with sqlite3.connect(sys.argv[1]) as con:
    con.execute(
        "update handoff_notifications set created_at = ? where id = ?",
        ("2000-01-01 00:00:00.000000 UTC", int(sys.argv[2])),
    )
PY

"$CLI" --db "$DB" notify retry "$RECOVERY_JOB" \
  --notification "$RECOVERY_INITIAL" \
  --role planning \
  --from-agent planner-main \
  --status failed \
  --reason host_rejected_recovery \
  --stale-after 1s \
  --detail "Recovery delivery failed." >/dev/null

test "$("$CLI" --db "$DB" notify list | list_ids)" = "4 3 2 1"
test "$("$CLI" --db "$DB" notify list --order oldest | list_ids)" = "1 2 3 4"
test "$("$CLI" --db "$DB" notify list --order newest --limit 2 | list_ids)" = "4 3"
test "$("$CLI" --db "$DB" notify list --after-id 2 | list_ids)" = "4 3"
test "$("$CLI" --db "$DB" notify list --before-id 3 | list_ids)" = "2 1"
test "$("$CLI" --db "$DB" notify list \
  --after-id 1 --before-id 4 --order newest --limit 2 | list_ids)" = "3 2"
test "$("$CLI" --db "$DB" notify list --job "$FIRST_JOB" | list_ids)" = "2 1"
test "$("$CLI" --db "$DB" notify list --status sent | list_ids)" = "3 2"
test "$("$CLI" --db "$DB" notify list --recovery-only | list_ids)" = "4"
"$CLI" --db "$DB" notify list --after-id 4 | grep '^No notifications\.$' >/dev/null

"$CLI" --db "$DB" notify list \
  --after-id 1 \
  --before-id 4 \
  --status sent \
  --order newest \
  --limit 1 \
  --format json >"$TMP/combined.json"
"$CLI" --db "$DB" notify list \
  --recovery-only \
  --format json >"$TMP/recovery.json"
python3 - "$TMP/combined.json" "$TMP/recovery.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    combined = json.load(handle)
with open(sys.argv[2], encoding="utf-8") as handle:
    recovery = json.load(handle)

if [row["id"] for row in combined] != [3]:
    raise SystemExit(f"unexpected combined JSON filter result: {combined}")
if len(recovery) != 1 or recovery[0]["id"] != 4:
    raise SystemExit(f"unexpected recovery JSON filter result: {recovery}")
if recovery[0]["retry_of_notification_id"] != 3:
    raise SystemExit(f"missing recovery linkage: {recovery}")
PY

for invalid in \
  "--limit 0" \
  "--limit -1" \
  "--after-id -1" \
  "--before-id 0" \
  "--after-id 3 --before-id 3" \
  "--after-id 4 --before-id 3"; do
  if "$CLI" --db "$DB" notify list $invalid >/dev/null 2>&1; then
    echo "ERROR: notify list accepted invalid range: $invalid" >&2
    exit 1
  fi
done

"$CLI" --db "$DB" migrate --check | grep 'schema=14' >/dev/null

echo "OK RC9 notification list filters db=$DB"
