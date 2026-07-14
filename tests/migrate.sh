#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CLI="$ROOT/bin/baton"
TMP="$(mktemp -d /tmp/baton-migrate.XXXXXX)"
DB="$TMP/legacy.sqlite3"
CR_DIR="$TMP/change-requests"

snapshot_workflow_data() {
  python3 - "$1" <<'PY'
import json
import sqlite3
import sys

tables = (
    "handoff_jobs",
    "handoff_dependencies",
    "handoff_events",
    "handoff_controls",
    "change_requests",
    "cr_events",
    "cr_handoffs",
)
with sqlite3.connect(sys.argv[1]) as con:
    con.row_factory = sqlite3.Row
    snapshot = {}
    for table in tables:
        rows = con.execute(f"select * from {table} order by rowid").fetchall()
        snapshot[table] = [dict(row) for row in rows]
    snapshot["custom_roles"] = [
        dict(row)
        for row in con.execute(
            "select * from roles where role_id = 'migration-custom' order by role_id"
        )
    ]
    snapshot["custom_aliases"] = [
        dict(row)
        for row in con.execute(
            "select * from role_aliases where role_id = 'migration-custom' order by alias"
        )
    ]
    snapshot["custom_permissions"] = [
        dict(row)
        for row in con.execute(
            "select * from role_permissions where role_id = 'migration-custom' order by permission"
        )
    ]
print(json.dumps(snapshot, sort_keys=True, separators=(",", ":")))
PY
}

"$CLI" --db "$DB" init >/dev/null
"$CLI" --db "$DB" role add migration-custom --display-name "Migration Custom" >/dev/null
"$CLI" --db "$DB" role alias-add mc migration-custom >/dev/null
"$CLI" --db "$DB" role permission-add migration-custom cr.review >/dev/null
JOB_ID="$("$CLI" --db "$DB" register \
  --title "Preserve migration handoff" \
  --role backend \
  --objective "Verify migration preserves workflow data." \
  --exit-criteria "All workflow rows are unchanged." | awk '{print $1}')"
"$CLI" --db "$DB" claim "$JOB_ID" --role backend --claimed-by migration-test >/dev/null
CR_ID="$("$CLI" --db "$DB" cr create \
  --title "Preserve migration CR" \
  --author-role planning \
  --reviewer-role sm \
  --dir "$CR_DIR" | awk '{print $1}')"
"$CLI" --db "$DB" cr submit "$CR_ID" --role planning >/dev/null
"$CLI" --db "$DB" shift start --role backend --duration 1h >/dev/null

python3 - "$DB" <<'PY'
import sqlite3
import sys

with sqlite3.connect(sys.argv[1]) as con:
    con.execute("drop table schema_migrations")
    con.execute(
        "delete from role_permissions where role_id = 'sm' and permission = 'cr.admin'"
    )
PY

BEFORE="$(snapshot_workflow_data "$DB")"
"$CLI" --db "$DB" migrate | grep 'schema=0->1 applied=1:initial_schema' >/dev/null
AFTER="$(snapshot_workflow_data "$DB")"
if [[ "$BEFORE" != "$AFTER" ]]; then
  echo "ERROR: workflow data changed during migration" >&2
  exit 1
fi
"$CLI" --db "$DB" role permission-list sm | grep 'cr.admin' >/dev/null
python3 - "$DB" <<'PY'
import sqlite3
import sys

with sqlite3.connect(sys.argv[1]) as con:
    row = con.execute(
        "select version, name from schema_migrations order by version"
    ).fetchall()
if row != [(1, "initial_schema")]:
    raise SystemExit(f"unexpected migration records: {row}")
PY

"$CLI" --db "$DB" migrate | grep 'schema=1->1 applied=none' >/dev/null
if [[ "$(snapshot_workflow_data "$DB")" != "$AFTER" ]]; then
  echo "ERROR: repeated migration changed workflow data" >&2
  exit 1
fi

python3 - "$DB" <<'PY'
import sqlite3
import sys

with sqlite3.connect(sys.argv[1]) as con:
    con.execute(
        "insert into schema_migrations(version, name, applied_at) values (999, 'future', 'future')"
    )
PY
if "$CLI" --db "$DB" status >/dev/null 2>&1; then
  echo "ERROR: older Baton accepted a newer database schema" >&2
  exit 1
fi
if [[ "$(snapshot_workflow_data "$DB")" != "$AFTER" ]]; then
  echo "ERROR: newer schema rejection changed workflow data" >&2
  exit 1
fi

AUTO_DB="$TMP/automatic.sqlite3"
"$CLI" --db "$AUTO_DB" init >/dev/null
python3 - "$AUTO_DB" <<'PY'
import sqlite3
import sys

with sqlite3.connect(sys.argv[1]) as con:
    con.execute("drop table schema_migrations")
PY
"$CLI" --db "$AUTO_DB" status >/dev/null
python3 - "$AUTO_DB" <<'PY'
import sqlite3
import sys

with sqlite3.connect(sys.argv[1]) as con:
    version = con.execute("select max(version) from schema_migrations").fetchone()[0]
if version != 1:
    raise SystemExit(f"automatic migration did not apply: {version}")
PY

ROLLBACK_DB="$TMP/rollback.sqlite3"
python3 - "$ROLLBACK_DB" <<'PY'
import sqlite3
import sys

with sqlite3.connect(sys.argv[1]) as con:
    con.execute("create table roles (role_id text primary key)")
    con.execute("insert into roles(role_id) values ('sentinel')")
PY
if "$CLI" --db "$ROLLBACK_DB" migrate >/dev/null 2>&1; then
  echo "ERROR: incompatible schema migration unexpectedly succeeded" >&2
  exit 1
fi
python3 - "$ROLLBACK_DB" <<'PY'
import sqlite3
import sys

with sqlite3.connect(sys.argv[1]) as con:
    tables = {
        row[0]
        for row in con.execute(
            "select name from sqlite_master where type = 'table' and name not like 'sqlite_%'"
        )
    }
    roles = con.execute("select role_id from roles").fetchall()
if tables != {"roles"}:
    raise SystemExit(f"migration was not rolled back: {sorted(tables)}")
if roles != [("sentinel",)]:
    raise SystemExit(f"existing data changed after rollback: {roles}")
PY

echo "OK migrate db=$DB rollback_db=$ROLLBACK_DB"
