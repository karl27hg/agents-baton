#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CLI="$ROOT/bin/baton"
TMP="$(mktemp /tmp/baton-shift.XXXXXX)"
DB="$TMP.sqlite3"

"$CLI" --db "$DB" init >/dev/null
"$CLI" --db "$DB" shift start --role backend | grep "role:backend" | grep "active" >/dev/null
BACKEND_UNTIL="$("$CLI" --db "$DB" shift status --role backend | awk -F 'until=' '/role:backend/ {print $2}' | awk -F '\t' '{print $1}')"
python3 - "$BACKEND_UNTIL" <<'PY'
from datetime import datetime, timezone
import sys
until = datetime.fromisoformat(sys.argv[1].removesuffix(" UTC") + "+00:00")
delta = (until - datetime.now(timezone.utc)).total_seconds()
if not 3.9 * 3600 <= delta <= 4.1 * 3600:
    raise SystemExit(f"unexpected default start duration seconds={delta}")
PY
"$CLI" --db "$DB" shift extend --role backend | grep "role:backend" | grep "active" >/dev/null
BACKEND_EXTENDED_UNTIL="$("$CLI" --db "$DB" shift status --role backend | awk -F 'until=' '/role:backend/ {print $2}' | awk -F '\t' '{print $1}')"
python3 - "$BACKEND_UNTIL" "$BACKEND_EXTENDED_UNTIL" <<'PY'
from datetime import datetime, timezone
import sys
before = datetime.fromisoformat(sys.argv[1].removesuffix(" UTC") + "+00:00")
after = datetime.fromisoformat(sys.argv[2].removesuffix(" UTC") + "+00:00")
delta = (after - before).total_seconds()
if not 0.9 * 3600 <= delta <= 1.1 * 3600:
    raise SystemExit(f"unexpected default extend duration seconds={delta}")
PY
"$CLI" --db "$DB" shift start --role frontend --duration 2s | grep "role:frontend" | grep "active" >/dev/null

JOB1="$("$CLI" --db "$DB" register \
  --title "Finish after shift expiry" \
  --role frontend \
  --objective "Verify finish is allowed after shift expiry." \
  --exit-criteria "The in-progress job can be finished." | awk '{print $1}')"
"$CLI" --db "$DB" claim "$JOB1" --role frontend --claimed-by shift-a >/dev/null
sleep 3
"$CLI" --db "$DB" finish "$JOB1" --role frontend --evidence "Finished after shift expiry." >/dev/null

JOB2="$("$CLI" --db "$DB" register \
  --title "Claim blocked after shift expiry" \
  --role frontend \
  --objective "Verify new claims are blocked after shift expiry." \
  --exit-criteria "Claim fails until the shift is extended." | awk '{print $1}')"
if "$CLI" --db "$DB" claim "$JOB2" --role frontend --claimed-by shift-b >/dev/null 2>&1; then
  echo "ERROR: claim after shift expiry unexpectedly succeeded" >&2
  exit 1
fi
"$CLI" --db "$DB" shift status --role frontend | grep "role:frontend" | grep "expired" >/dev/null
"$CLI" --db "$DB" resume --role frontend >/dev/null
if "$CLI" --db "$DB" claim "$JOB2" --role frontend --claimed-by shift-resume-only >/dev/null 2>&1; then
  echo "ERROR: resume without shift extension unexpectedly allowed claim after expiry" >&2
  exit 1
fi

set +e
"$CLI" --db "$DB" wait --role frontend --timeout 1 --interval 1 >/tmp/baton-shift-wait.out 2>&1
WAIT_STATUS="$?"
set -e
if [[ "$WAIT_STATUS" -ne 3 ]]; then
  echo "ERROR: expected wait exit status 3 after shift expiry, got $WAIT_STATUS" >&2
  cat /tmp/baton-shift-wait.out >&2
  exit 1
fi

"$CLI" --db "$DB" shift extend --role frontend --duration 1m | grep "active" >/dev/null
"$CLI" --db "$DB" claim "$JOB2" --role frontend --claimed-by shift-b >/dev/null

"$CLI" --db "$DB" shift start --role qa --duration 1m >/dev/null
"$CLI" --db "$DB" stop --role qa --reason "manual stop while shift active" >/dev/null
JOB3="$("$CLI" --db "$DB" register \
  --title "Manual stop dominates active shift" \
  --role qa \
  --objective "Verify manual stop blocks claims even while shift is active." \
  --exit-criteria "Claim fails until resume." | awk '{print $1}')"
if "$CLI" --db "$DB" claim "$JOB3" --role qa --claimed-by shift-c >/dev/null 2>&1; then
  echo "ERROR: claim during manual stop unexpectedly succeeded" >&2
  exit 1
fi
"$CLI" --db "$DB" resume --role qa >/dev/null
"$CLI" --db "$DB" claim "$JOB3" --role qa --claimed-by shift-c >/dev/null

AMBIG_DB="$TMP.ambiguous.sqlite3"
"$CLI" --db "$AMBIG_DB" init >/dev/null
"$CLI" --db "$AMBIG_DB" shift start --all --duration 1s >/dev/null
"$CLI" --db "$AMBIG_DB" shift start --role qa --duration 1m >/dev/null
sleep 2
JOB4="$("$CLI" --db "$AMBIG_DB" register \
  --title "All shift expiry dominates role shift" \
  --role qa \
  --objective "Verify expired all scope blocks a still-active role scope." \
  --exit-criteria "Claim fails until all scope is extended." | awk '{print $1}')"
if "$CLI" --db "$AMBIG_DB" claim "$JOB4" --role qa --claimed-by shift-d >/dev/null 2>&1; then
  echo "ERROR: role claim unexpectedly ignored expired all scope" >&2
  exit 1
fi
"$CLI" --db "$AMBIG_DB" shift status --role qa | grep "^all" | grep "expired" >/dev/null
"$CLI" --db "$AMBIG_DB" shift status --role qa | grep "role:qa" | grep "active" >/dev/null
"$CLI" --db "$AMBIG_DB" shift extend --role qa --duration 1m >/dev/null
if "$CLI" --db "$AMBIG_DB" claim "$JOB4" --role qa --claimed-by shift-e >/dev/null 2>&1; then
  echo "ERROR: role extension unexpectedly bypassed expired all scope" >&2
  exit 1
fi
"$CLI" --db "$AMBIG_DB" shift extend --all --duration 1m >/dev/null
"$CLI" --db "$AMBIG_DB" claim "$JOB4" --role qa --claimed-by shift-f >/dev/null

echo "OK shift db=$DB"
