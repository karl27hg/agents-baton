#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CLI="$ROOT/bin/baton"
TMP="$(mktemp /tmp/baton-update.XXXXXX)"
DB="$TMP.sqlite3"

"$CLI" --db "$DB" init >/dev/null

python3 - "$DB" <<'PY'
import sqlite3
import sys

with sqlite3.connect(sys.argv[1]) as con:
    con.execute(
        "delete from role_permissions where role_id = 'sm' and permission = 'cr.admin'"
    )
PY

if "$CLI" --db "$DB" role permission-list sm | grep "cr.admin" >/dev/null; then
  echo "ERROR: test setup failed; cr.admin still exists" >&2
  exit 1
fi

"$CLI" --db "$DB" update | grep "Updated" >/dev/null
"$CLI" --db "$DB" role permission-list sm | grep "cr.admin" >/dev/null

"$CLI" --db "$DB" update | grep "Updated" >/dev/null
"$CLI" --db "$DB" role permission-list sm | grep "cr.admin" >/dev/null

echo "OK update db=$DB"
