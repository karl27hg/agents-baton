#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CLI="$ROOT/bin/baton"
TMP="$(mktemp -d /tmp/baton-auto-interval.XXXXXX)"
DB="$TMP/baton.sqlite3"

"$CLI" --db "$DB" init >/dev/null

python3 - "$CLI" "$DB" <<'PY'
import runpy
import sqlite3
import sys

cli, db = sys.argv[1:]
module = runpy.run_path(cli, run_name="baton_module")

automatic_poll_interval = module["automatic_poll_interval"]
poll_sleep_seconds = module["poll_sleep_seconds"]
parse_poll_interval = module["parse_poll_interval"]
waiter_lease_seconds = module["waiter_lease_seconds"]

assert parse_poll_interval("auto") is None
assert parse_poll_interval("3") == 3
assert automatic_poll_interval(0) == 3
assert automatic_poll_interval(1) == 3
assert automatic_poll_interval(2) == 6
assert automatic_poll_interval(9) == 27
assert automatic_poll_interval(10) == 30
assert automatic_poll_interval(100) == 30

waiter_id = "00000000-0000-0000-0000-000000000123"
for count in (1, 2, 10, 100):
    target = automatic_poll_interval(count)
    actual = poll_sleep_seconds(None, count, waiter_id)
    assert target * 0.9 <= actual <= target * 0.95, (count, target, actual)
assert poll_sleep_seconds(7, 100, waiter_id) == 7.0
assert waiter_lease_seconds(None) == 30
assert waiter_lease_seconds(7) == 30
assert waiter_lease_seconds(60) == 65

with sqlite3.connect(db) as con:
    columns = {
        row[1]
        for row in con.execute("pragma table_info(waiter_leases)")
    }
expected = {
    "waiter_id",
    "wait_kind",
    "role_id",
    "started_at",
    "heartbeat_at",
    "lease_expires_at",
}
assert columns == expected, columns
PY

set +e
"$CLI" --db "$DB" wait --role frontend --timeout 30 >"$TMP/handoff-auto.out" 2>&1 &
HANDOFF_PID="$!"
"$CLI" --db "$DB" cr wait-review --role sm --timeout 30 --interval auto >"$TMP/cr-auto.out" 2>&1 &
CR_PID="$!"
"$CLI" --db "$DB" wait --role backend --timeout 30 --interval 1 >"$TMP/handoff-fixed.out" 2>&1 &
FIXED_PID="$!"
set -e

FOUND=0
for _ in {1..50}; do
  if python3 - "$DB" <<'PY'
import sqlite3
import sys

with sqlite3.connect(sys.argv[1]) as con:
    rows = con.execute(
        "select wait_kind, count(*) from waiter_leases group by wait_kind order by wait_kind"
    ).fetchall()
raise SystemExit(0 if rows == [("cr_review", 1), ("handoff", 2)] else 1)
PY
  then
    FOUND=1
    break
  fi
  sleep 0.1
done

if [[ "$FOUND" -ne 1 ]]; then
  echo "ERROR: active handoff and CR waiters were not registered" >&2
  exit 1
fi

"$CLI" --db "$DB" stop --all --reason "auto interval test" >/dev/null
set +e
wait "$HANDOFF_PID"
HANDOFF_STATUS="$?"
wait "$CR_PID"
CR_STATUS="$?"
wait "$FIXED_PID"
FIXED_STATUS="$?"
set -e

if [[ "$HANDOFF_STATUS" -ne 3 || "$CR_STATUS" -ne 3 || "$FIXED_STATUS" -ne 3 ]]; then
  echo "ERROR: waiter did not exit with stopped status: handoff=$HANDOFF_STATUS cr=$CR_STATUS fixed=$FIXED_STATUS" >&2
  exit 1
fi

python3 - "$DB" <<'PY'
import sqlite3
import sys

with sqlite3.connect(sys.argv[1]) as con:
    count = con.execute("select count(*) from waiter_leases").fetchone()[0]
assert count == 0, count
PY

"$CLI" --db "$DB" resume --all >/dev/null
python3 - "$DB" <<'PY'
import sqlite3
import sys

with sqlite3.connect(sys.argv[1]) as con:
    con.execute(
        """
        insert into waiter_leases(
          waiter_id, wait_kind, role_id, started_at, heartbeat_at, lease_expires_at
        ) values ('stale', 'handoff', 'frontend', '2000-01-01 00:00:00 UTC',
                  '2000-01-01 00:00:00 UTC', '2000-01-01 00:00:30 UTC')
        """
    )
PY

set +e
"$CLI" --db "$DB" wait --role frontend --timeout 1 --interval auto >"$TMP/stale.out" 2>&1
STALE_STATUS="$?"
set -e
if [[ "$STALE_STATUS" -ne 2 ]]; then
  echo "ERROR: stale lease wait did not time out normally: $STALE_STATUS" >&2
  exit 1
fi

python3 - "$DB" <<'PY'
import sqlite3
import sys

with sqlite3.connect(sys.argv[1]) as con:
    count = con.execute("select count(*) from waiter_leases").fetchone()[0]
assert count == 0, count
PY

python3 - "$DB" <<'PY'
import sqlite3
import sys

with sqlite3.connect(sys.argv[1]) as con:
    for index in range(9):
        con.execute(
            """
            insert into waiter_leases(
              waiter_id, wait_kind, role_id, started_at, heartbeat_at, lease_expires_at
            ) values (?, 'handoff', 'qa', '2099-01-01 00:00:00 UTC',
                      '2099-01-01 00:00:00 UTC', '2099-01-01 00:00:30 UTC')
            """,
            (f"future-{index}",),
        )
PY

START="$(python3 -c 'import time; print(time.monotonic())')"
set +e
"$CLI" --db "$DB" wait --role frontend --timeout 1 >"$TMP/bounded.out" 2>&1
BOUNDED_STATUS="$?"
set -e
END="$(python3 -c 'import time; print(time.monotonic())')"

python3 - "$DB" "$START" "$END" "$BOUNDED_STATUS" <<'PY'
import sqlite3
import sys

db, start, end, status = sys.argv[1:]
elapsed = float(end) - float(start)
assert int(status) == 2, status
assert elapsed < 3, elapsed
with sqlite3.connect(db) as con:
    con.execute("delete from waiter_leases")
PY

echo "OK auto interval db=$DB"
