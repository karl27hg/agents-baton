#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CLI="$ROOT/bin/baton"
TMP="$(mktemp -d /tmp/baton-opt-in-notify.XXXXXX)"
DB="$TMP/baton.sqlite3"

"$CLI" --db "$DB" init >/dev/null

FRONTEND_SESSION="$("$CLI" --db "$DB" agent session-set \
  --role frontend \
  --agent-id frontend-main \
  --thread-id codex-thread-frontend \
  --model gpt-5.6-sol | awk -F '\t' '{print $1}')"
BACKEND_SESSION="$("$CLI" --db "$DB" agent session-set \
  --role backend \
  --agent-id backend-main \
  --thread-id codex-thread-backend \
  --model gpt-5.6-terra | awk -F '\t' '{print $1}')"

if "$CLI" --db "$DB" agent session-set \
  --role qa \
  --agent-id qa-main \
  --thread-id codex-thread-backend \
  --model gpt-5.6-luna >/dev/null 2>&1; then
  echo "ERROR: one Codex thread was bound to two agents" >&2
  exit 1
fi
if "$CLI" --db "$DB" agent session-set \
  --role frontend \
  --agent-id frontend-main \
  --thread-id codex-thread-frontend-new \
  --model gpt-5.6-sol >/dev/null 2>&1; then
  echo "ERROR: active session was replaced without --replace" >&2
  exit 1
fi
"$CLI" --db "$DB" agent session-list --status active \
  | grep "$BACKEND_SESSION.*backend-main.*codex-thread-backend.*gpt-5.6-terra" >/dev/null
"$CLI" --db "$DB" agent session-list --agent-id frontend-main --format json \
  | grep '"model": "gpt-5.6-sol"' >/dev/null

SOURCE_JOB="$("$CLI" --db "$DB" register \
  --title "Prepare backend contract" \
  --role frontend \
  --objective "Complete work that wakes a peer Codex task." \
  --exit-criteria "The dependent handoff becomes notification-ready." | awk '{print $1}')"
TARGET_JOB="$("$CLI" --db "$DB" register \
  --title "Implement backend contract" \
  --role backend \
  --depends-on "$SOURCE_JOB" \
  --objective "Continue after the peer notification." \
  --exit-criteria "The notified handoff is claimed and completed." | awk '{print $1}')"

"$CLI" --db "$DB" claim "$SOURCE_JOB" --role frontend --claimed-by frontend-main >/dev/null
"$CLI" --db "$DB" finish "$SOURCE_JOB" --role frontend --evidence "Contract ready." >/dev/null
"$CLI" --db "$DB" handoff show "$TARGET_JOB" | grep 'status: open' >/dev/null

BUSY_JOB="$("$CLI" --db "$DB" register \
  --title "Existing backend work" \
  --role backend \
  --objective "Keep the peer busy during initial target selection." \
  --exit-criteria "Busy peers are excluded until this work finishes." | awk '{print $1}')"
"$CLI" --db "$DB" claim "$BUSY_JOB" --role backend --claimed-by backend-main >/dev/null
"$CLI" --db "$DB" notify targets "$SOURCE_JOB" \
  --role frontend \
  --from-agent frontend-main \
  | grep "$TARGET_JOB.*backend.*no_active_peer_session" >/dev/null
"$CLI" --db "$DB" finish "$BUSY_JOB" --role backend --evidence "Peer is available again." >/dev/null

"$CLI" --db "$DB" shift end --role backend --reason "Backend shift ended." >/dev/null
"$CLI" --db "$DB" notify targets "$SOURCE_JOB" \
  --role frontend \
  --from-agent frontend-main \
  | grep "$TARGET_JOB.*backend.*outside_shift" >/dev/null
"$CLI" --db "$DB" shift start --role backend --duration 1s >/dev/null
sleep 2
"$CLI" --db "$DB" notify targets "$SOURCE_JOB" \
  --role frontend \
  --from-agent frontend-main \
  | grep "$TARGET_JOB.*backend.*outside_shift" >/dev/null
"$CLI" --db "$DB" shift start --role backend --duration 1h >/dev/null

"$CLI" --db "$DB" notify targets "$SOURCE_JOB" \
  --role frontend \
  --from-agent frontend-main \
  | grep "$TARGET_JOB.*backend.*candidate.*backend-main.*codex.*codex-thread-backend.*gpt-5.6-terra" >/dev/null
"$CLI" --db "$DB" handoff show "$TARGET_JOB" | grep 'status: open' >/dev/null

if "$CLI" --db "$DB" notify record "$TARGET_JOB" \
  --role frontend \
  --from-agent frontend-main \
  --to-agent backend-main \
  --status failed >/dev/null 2>&1; then
  echo "ERROR: failed notification was recorded without detail" >&2
  exit 1
fi
"$CLI" --db "$DB" notify record "$TARGET_JOB" \
  --role frontend \
  --from-agent frontend-main \
  --to-agent backend-main \
  --status failed \
  --detail "Codex message delivery returned an error." >/dev/null
"$CLI" --db "$DB" notify targets "$SOURCE_JOB" \
  --role frontend \
  --from-agent frontend-main \
  | grep "$TARGET_JOB.*candidate.*backend-main" >/dev/null

"$CLI" --db "$DB" notify record "$TARGET_JOB" \
  --role frontend \
  --from-agent frontend-main \
  --to-agent backend-main \
  --status sent \
  --message-ref codex-message-001 \
  --detail "Follow-up accepted by the Codex host." >/dev/null
"$CLI" --db "$DB" notify targets "$SOURCE_JOB" \
  --role frontend \
  --from-agent frontend-main \
  | grep "$TARGET_JOB.*already_notified.*backend-main" >/dev/null
if "$CLI" --db "$DB" notify record "$TARGET_JOB" \
  --role frontend \
  --from-agent frontend-main \
  --to-agent backend-main \
  --status sent >/dev/null 2>&1; then
  echo "ERROR: duplicate successful notification was recorded" >&2
  exit 1
fi

"$CLI" --db "$DB" notify list --job "$TARGET_JOB" | grep 'failed.*frontend-main.*backend-main' >/dev/null
"$CLI" --db "$DB" notify list --job "$TARGET_JOB" | grep 'host_accepted.*frontend-main.*backend-main' >/dev/null
"$CLI" --db "$DB" events "$TARGET_JOB" | grep 'notification_sent.*backend-main.*gpt-5.6-terra' >/dev/null
"$CLI" --db "$DB" claim "$TARGET_JOB" --role backend --claimed-by backend-main >/dev/null
"$CLI" --db "$DB" finish "$TARGET_JOB" --role backend --evidence "Notified work completed." >/dev/null

RETRY_SOURCE="$("$CLI" --db "$DB" register \
  --title "Prepare retry notification" \
  --role frontend \
  --objective "Open a handoff that will require a second notification." \
  --exit-criteria "The dependent handoff is ready." | awk '{print $1}')"
RETRY_TARGET="$("$CLI" --db "$DB" register \
  --title "Retry notification target" \
  --role backend \
  --depends-on "$RETRY_SOURCE" \
  --objective "Fail once and then complete on retry." \
  --exit-criteria "Both notification attempts are audited." | awk '{print $1}')"
"$CLI" --db "$DB" claim "$RETRY_SOURCE" --role frontend --claimed-by frontend-main >/dev/null
"$CLI" --db "$DB" finish "$RETRY_SOURCE" --role frontend \
  --evidence "Retry target is ready." >/dev/null
"$CLI" --db "$DB" notify record "$RETRY_TARGET" \
  --role frontend \
  --from-agent frontend-main \
  --to-agent backend-main \
  --status sent \
  --message-ref codex-retry-001 >/dev/null
"$CLI" --db "$DB" claim "$RETRY_TARGET" --role backend --claimed-by backend-main >/dev/null
FAILURE_LINE="$("$CLI" --db "$DB" fail "$RETRY_TARGET" \
  --role backend \
  --reason "First attempt needs remediation." \
  --evidence "The retry path is intentional." \
  --file-path "$TMP/retry-failure.md")"
FAILURE_CR="$(sed -n 's/.* cr=\([^ ]*\).*/\1/p' <<<"$FAILURE_LINE")"
"$CLI" --db "$DB" cr approve "$FAILURE_CR" --role planning \
  --evidence "Authorize a corrected retry." >/dev/null
"$CLI" --db "$DB" retry "$RETRY_TARGET" --role planning \
  --cr-id "$FAILURE_CR" \
  --reason "Use the corrected baseline." | grep 'attempt=2' >/dev/null
"$CLI" --db "$DB" handoff show "$RETRY_TARGET" | grep 'attempt: 2' >/dev/null
"$CLI" --db "$DB" notify targets "$RETRY_SOURCE" \
  --role frontend \
  --from-agent frontend-main \
  | grep "$RETRY_TARGET.*attempt=2.*candidate.*backend-main" >/dev/null
"$CLI" --db "$DB" notify record "$RETRY_TARGET" \
  --role frontend \
  --from-agent frontend-main \
  --to-agent backend-main \
  --status sent \
  --message-ref codex-retry-002 >/dev/null
"$CLI" --db "$DB" notify list --job "$RETRY_TARGET" \
  | grep 'attempt=1.*host_accepted.*frontend-main.*backend-main' >/dev/null
"$CLI" --db "$DB" notify list --job "$RETRY_TARGET" \
  | grep 'attempt=2.*host_accepted.*frontend-main.*backend-main' >/dev/null
"$CLI" --db "$DB" claim "$RETRY_TARGET" --role backend --claimed-by backend-main >/dev/null
"$CLI" --db "$DB" finish "$RETRY_TARGET" --role backend \
  --evidence "Second attempt completed." >/dev/null
"$CLI" --db "$DB" cr mark-implemented "$FAILURE_CR" --role planning \
  --evidence "Retried handoff completed." >/dev/null

"$CLI" --db "$DB" agent session-end \
  --agent-id backend-main \
  --reason "Codex task completed." | grep "$BACKEND_SESSION.*inactive.*backend-main" >/dev/null
"$CLI" --db "$DB" agent session-list --agent-id backend-main --status inactive \
  | grep 'codex-thread-backend.*gpt-5.6-terra' >/dev/null

test -n "$FRONTEND_SESSION"
echo "OK opt-in notification source=$SOURCE_JOB target=$TARGET_JOB db=$DB"
