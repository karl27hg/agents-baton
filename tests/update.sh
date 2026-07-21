#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CLI="$ROOT/bin/baton"
TMP="$(mktemp /tmp/baton-update.XXXXXX)"
DB="$TMP.sqlite3"

"$CLI" --db "$DB" init >/dev/null

"$CLI" --db "$DB" role permission-remove sm cr.admin | grep "Removed permission" >/dev/null

if "$CLI" --db "$DB" role permission-list sm | grep "cr.admin" >/dev/null; then
  echo "ERROR: test setup failed; cr.admin still exists" >&2
  exit 1
fi

"$CLI" --db "$DB" update 2>"$TMP.warning" | grep "Updated" >/dev/null
grep "deprecated database migration alias" "$TMP.warning" >/dev/null
if "$CLI" --db "$DB" role permission-list sm | grep "cr.admin" >/dev/null; then
  echo "ERROR: update restored a project-revoked permission" >&2
  exit 1
fi
"$CLI" --db "$DB" role permission-list sm | grep "handoff.cancel" >/dev/null

"$CLI" --db "$DB" update 2>/dev/null | grep "Updated" >/dev/null
if "$CLI" --db "$DB" role permission-list sm | grep "cr.admin" >/dev/null; then
  echo "ERROR: repeated update restored a project-revoked permission" >&2
  exit 1
fi
"$CLI" --db "$DB" role permission-add sm cr.admin >/dev/null
"$CLI" --db "$DB" role permission-list sm | grep "cr.admin" >/dev/null

echo "OK update db=$DB"
