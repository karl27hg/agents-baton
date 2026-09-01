#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CLI="$ROOT/bin/baton"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/baton-help.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT

"$CLI" -h >"$TMP/root-short.txt"
"$CLI" --help >"$TMP/root-long.txt"
"$CLI" help >"$TMP/root-command.txt"
cmp "$TMP/root-short.txt" "$TMP/root-long.txt"
cmp "$TMP/root-short.txt" "$TMP/root-command.txt"
grep 'help.*show command help' "$TMP/root-command.txt" >/dev/null
grep "baton guide list" "$TMP/root-command.txt" >/dev/null
grep "baton guide show" "$TMP/root-command.txt" >/dev/null
grep "bootstrap|worker|planner" "$TMP/root-command.txt" >/dev/null

"$CLI" project migrate -h >"$TMP/migrate-short.txt"
"$CLI" help project migrate >"$TMP/migrate-command.txt"
cmp "$TMP/migrate-short.txt" "$TMP/migrate-command.txt"
grep -- '--source-db SOURCE_DB' "$TMP/migrate-command.txt" >/dev/null
grep -- '--plan-token PLAN_TOKEN' "$TMP/migrate-command.txt" >/dev/null

"$CLI" help guide show | grep '{bootstrap,worker,planner}' >/dev/null
if "$CLI" help unknown-command >/dev/null 2>&1; then
  echo "ERROR: help accepted an unknown command" >&2
  exit 1
fi

echo "OK command help"
