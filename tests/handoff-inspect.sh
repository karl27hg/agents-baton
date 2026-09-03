#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CLI="$ROOT/bin/baton"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/baton-handoff-inspect.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
DB="$TMP/baton.sqlite3"

"$CLI" --db "$DB" init >/dev/null
UPSTREAM="$("$CLI" --db "$DB" register \
  --title "Inspection upstream" \
  --role backend \
  --source-ref "docs/source.md" \
  --objective "Produce the shared contract." \
  --exit-criteria "The contract is reviewed." | awk '{print $1}')"
DOWNSTREAM="$("$CLI" --db "$DB" register \
  --title "Inspection downstream" \
  --role frontend \
  --depends-on "$UPSTREAM" \
  --source-ref "docs/downstream.md" \
  --objective "Consume the shared contract." \
  --exit-criteria "The client integration passes." | awk '{print $1}')"

SHOW_OUTPUT="$("$CLI" --db "$DB" handoff show "$DOWNSTREAM")"
printf '%s\n' "$SHOW_OUTPUT" | grep "objective: Consume the shared contract." >/dev/null
printf '%s\n' "$SHOW_OUTPUT" | grep "exit_criteria: The client integration passes." >/dev/null
printf '%s\n' "$SHOW_OUTPUT" | grep "source_ref: docs/downstream.md" >/dev/null
printf '%s\n' "$SHOW_OUTPUT" | grep "depends_on: $UPSTREAM" >/dev/null
"$CLI" --db "$DB" handoff show "$DOWNSTREAM" --format json | grep '"status": "blocked"' >/dev/null

"$CLI" --db "$DB" handoff list --role frontend --status blocked | grep "$DOWNSTREAM" >/dev/null
if "$CLI" --db "$DB" handoff list --role backend --status blocked | grep "$DOWNSTREAM" >/dev/null; then
  echo "ERROR: handoff list role filter leaked another role" >&2
  exit 1
fi
if "$CLI" --db "$DB" handoff list --limit 0 >/dev/null 2>&1; then
  echo "ERROR: handoff list accepted a non-positive limit" >&2
  exit 1
fi

echo "OK handoff inspection upstream=$UPSTREAM downstream=$DOWNSTREAM"
