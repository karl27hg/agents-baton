#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CLI="$ROOT/bin/baton"
REPORT="$ROOT/bin/baton-report"
TMP="$(mktemp -d /tmp/baton-report.XXXXXX)"
DB="$TMP/baton.sqlite3"

"$CLI" --db "$DB" init >/dev/null

JOB="$("$CLI" --db "$DB" register \
  --title "Report smoke" \
  --role frontend \
  --objective "Generate report events." \
  --exit-criteria "Report events exist." | awk '{print $1}')"

"$CLI" --db "$DB" claim "$JOB" --role frontend --claimed-by report-agent >/dev/null
"$CLI" --db "$DB" finish "$JOB" --role frontend --evidence "Report evidence." >/dev/null
python3 - "$DB" "$JOB" <<'PY'
import sqlite3
import sys

with sqlite3.connect(sys.argv[1]) as con:
    con.execute(
        "update handoff_events set created_at = '2026-01-01 00:00:00 UTC' where job_id = ?",
        (sys.argv[2],),
    )
PY

CR="$("$CLI" --db "$DB" cr create --title "Report CR" --author-role planning --dir "$TMP/cr" | awk '{print $1}')"
"$CLI" --db "$DB" cr submit "$CR" --role planning >/dev/null
"$CLI" --db "$DB" gate create report-ready --role planning >/dev/null
"$CLI" --db "$DB" gate release report-ready --role planning --evidence "Report gate evidence." >/dev/null

"$REPORT" --db "$DB" audit --job "$JOB" | grep "$JOB" | grep finished >/dev/null
test "$("$REPORT" --db "$DB" audit --job "$JOB" | awk -F'\t' '{printf "%s%s", separator, $4; separator=","} END {print ""}')" = "registered,claimed,finished"
"$REPORT" --db "$DB" audit --cr "$CR" | grep "$CR" | grep submitted >/dev/null
"$REPORT" --db "$DB" audit --gate report-ready | grep report-ready | grep released >/dev/null
"$REPORT" --db "$DB" audit --role frontend | grep report-agent >/dev/null
"$REPORT" --db "$DB" audit --format json | grep '"source": "handoff"' >/dev/null
"$REPORT" --db "$DB" audit --format csv | grep "created_at,source,target_id" >/dev/null
"$REPORT" --db "$DB" summary | grep "Handoffs:" >/dev/null
"$REPORT" --db "$DB" summary | grep "finished: 1" >/dev/null
"$REPORT" --db "$DB" summary | grep "Gates:" >/dev/null
"$REPORT" --db "$DB" summary | grep "released: 1" >/dev/null
"$REPORT" --db "$DB" summary --format json | grep '"handoffs"' >/dev/null
"$REPORT" --db "$DB" summary --format json | grep '"gates"' >/dev/null

PENDING_DB="$TMP/pending.sqlite3"
python3 - "$DB" "$PENDING_DB" <<'PY'
import sqlite3
import sys

with sqlite3.connect(sys.argv[1]) as source, sqlite3.connect(sys.argv[2]) as target:
    source.backup(target)
with sqlite3.connect(sys.argv[2]) as con:
    con.execute("delete from schema_migrations where version = 6")
    con.execute("drop table workspace_events")
PY
if "$REPORT" --db "$PENDING_DB" summary >/dev/null 2>&1; then
  echo "ERROR: report accepted a database with pending migrations" >&2
  exit 1
fi

echo "OK report db=$DB"
