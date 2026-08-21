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

CR="$("$CLI" --db "$DB" cr create --title "Report CR" --author-role planning --dir "$TMP/cr" | awk '{print $1}')"
"$CLI" --db "$DB" cr submit "$CR" --role planning >/dev/null
"$CLI" --db "$DB" gate create report-ready --role planning >/dev/null
"$CLI" --db "$DB" gate release report-ready --role planning --evidence "Report gate evidence." >/dev/null

"$REPORT" --db "$DB" audit --job "$JOB" | grep "$JOB" | grep finished >/dev/null
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

echo "OK report db=$DB"
