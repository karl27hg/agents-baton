#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CLI="$ROOT/bin/baton"
TMP="$(mktemp /tmp/baton-smoke.XXXXXX)"
DB="$TMP.sqlite3"

"$CLI" --db "$DB" init >/dev/null
"$CLI" --db "$DB" role add content-design --display-name "Content Design" >/dev/null
"$CLI" --db "$DB" role alias-add fe frontend >/dev/null

JOB1="$("$CLI" --db "$DB" register \
  --title "Frontend smoke" \
  --role fe \
  --objective "Run frontend smoke handoff." \
  --exit-criteria "Frontend smoke is finished." | awk '{print $1}')"

"$CLI" --db "$DB" next --role frontend | grep "$JOB1" >/dev/null
"$CLI" --db "$DB" claim "$JOB1" --role frontend --claimed-by smoke-a >/dev/null
if "$CLI" --db "$DB" claim "$JOB1" --role frontend --claimed-by smoke-b >/tmp/baton-claim-race.out 2>&1; then
  echo "ERROR: duplicate claim unexpectedly succeeded" >&2
  exit 1
fi
"$CLI" --db "$DB" finish "$JOB1" --role frontend --evidence "Smoke evidence." >/dev/null

JOB2="$("$CLI" --db "$DB" register \
  --title "QA smoke" \
  --role qa \
  --depends-on "$JOB1" \
  --objective "Run QA smoke handoff." \
  --exit-criteria "QA smoke is finished." | awk '{print $1}')"

"$CLI" --db "$DB" promote-ready | grep "$JOB2" >/dev/null
"$CLI" --db "$DB" next --role qa | grep "$JOB2" >/dev/null
"$CLI" --db "$DB" events "$JOB2" | grep promoted >/dev/null

echo "OK smoke db=$DB"
