#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CLI="$ROOT/baton"
TMP="$(mktemp /tmp/baton-agent-id.XXXXXX)"
DB="$TMP.sqlite3"
AGENT_FILE="$TMP.agent-id"

"$CLI" --db "$DB" init >/dev/null
"$CLI" --db "$DB" --agent-id-file "$AGENT_FILE" agent init --role frontend --label test-agent >/dev/null

AGENT_ID="$("$CLI" --db "$DB" --agent-id-file "$AGENT_FILE" agent show)"
if [[ "$AGENT_ID" != "frontend-test-agent" ]]; then
  echo "ERROR: unexpected agent id: $AGENT_ID" >&2
  exit 1
fi

JOB="$("$CLI" --db "$DB" register \
  --title "Agent identity smoke" \
  --role frontend \
  --objective "Verify agent identity fallback." \
  --exit-criteria "Claim event records local agent id." | awk '{print $1}')"

"$CLI" --db "$DB" --agent-id-file "$AGENT_FILE" claim "$JOB" --role frontend >/dev/null
"$CLI" --db "$DB" events "$JOB" | grep claimed | grep frontend-test-agent >/dev/null

echo "OK agent id job=$JOB agent=$AGENT_ID db=$DB"
