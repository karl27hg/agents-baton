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
    "handoff_gate_dependencies",
    "handoff_events",
    "handoff_controls",
    "change_requests",
    "cr_events",
    "cr_handoffs",
    "workflow_gates",
    "gate_owners",
    "gate_events",
    "workspace_events",
    "handoff_failure_reviews",
    "agent_sessions",
    "handoff_notifications",
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
            """
            select * from role_permissions
            where role_id = 'migration-custom' and permission != 'handoff.register'
            order by permission
            """
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
CR_ID="$("$CLI" --db "$DB" cr create \
  --title "Preserve migration CR" \
  --author-role planning \
  --reviewer-role sm \
  --dir "$CR_DIR" | awk '{print $1}')"
"$CLI" --db "$DB" cr submit "$CR_ID" --role planning >/dev/null
"$CLI" --db "$DB" shift start --role backend --duration 1h >/dev/null

BATON_VERSION="$("$CLI" --version | awk '{print $2}')"
python3 - "$DB" "$BATON_VERSION" <<'PY'
import sqlite3
import sys

with sqlite3.connect(sys.argv[1]) as con:
    metadata = dict(con.execute("select key, value from database_metadata"))
    con.execute(
        """
        insert into workspace_events(
          entity_type, entity_id, operation, policy, outcome, head_commit,
          dirty, actor_role, created_at
        ) values ('handoff', 'migration-sentinel', 'registered', 'warn',
                  'accepted', 'abc123', 0, 'sm', '2026-01-01 00:00:00 UTC')
        """
    )
if metadata.get("created_with_baton_version") != sys.argv[2]:
    raise SystemExit(f"fresh database creation version missing: {metadata}")
if metadata.get("last_migrated_with_baton_version") != sys.argv[2]:
    raise SystemExit(f"fresh database migration version missing: {metadata}")
PY

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
"$CLI" --db "$DB" migrate | grep 'schema=0->10 applied=1:initial_schema,2:handoff_cancel_permission,3:named_gates,4:waiter_leases,5:database_metadata,6:workspace_provenance,7:handoff_failures,8:cr_body_integrity,9:plan_revision_controls,10:opt_in_thread_notifications' >/dev/null
AFTER="$(snapshot_workflow_data "$DB")"
if [[ "$BEFORE" != "$AFTER" ]]; then
  echo "ERROR: workflow data changed during migration" >&2
  exit 1
fi
"$CLI" --db "$DB" role permission-list sm | grep 'cr.admin' >/dev/null
"$CLI" --db "$DB" role permission-list sm | grep 'handoff.cancel' >/dev/null
"$CLI" --db "$DB" role permission-list sm | grep 'gate.manage' >/dev/null
"$CLI" --db "$DB" role permission-list sm | grep 'workspace.override' >/dev/null
"$CLI" --db "$DB" role permission-list migration-custom | grep 'handoff.register' >/dev/null
python3 - "$DB" <<'PY'
import sqlite3
import sys

with sqlite3.connect(sys.argv[1]) as con:
    row = con.execute(
        "select version, name from schema_migrations order by version"
    ).fetchall()
if row != [
    (1, "initial_schema"),
    (2, "handoff_cancel_permission"),
    (3, "named_gates"),
    (4, "waiter_leases"),
    (5, "database_metadata"),
    (6, "workspace_provenance"),
    (7, "handoff_failures"),
    (8, "cr_body_integrity"),
    (9, "plan_revision_controls"),
    (10, "opt_in_thread_notifications"),
]:
    raise SystemExit(f"unexpected migration records: {row}")
PY

"$CLI" --db "$DB" migrate | grep 'schema=10->10 applied=none' >/dev/null
"$CLI" --db "$DB" migrate --check | grep 'schema=10' >/dev/null
chmod 444 "$DB"
"$CLI" --db "$DB" migrate --check | grep 'schema=10' >/dev/null
chmod 644 "$DB"
if [[ "$(snapshot_workflow_data "$DB")" != "$AFTER" ]]; then
  echo "ERROR: repeated migration changed workflow data" >&2
  exit 1
fi

SAFETY_DB="$TMP/active-migration.sqlite3"
"$CLI" --db "$SAFETY_DB" init >/dev/null
SAFETY_JOB="$("$CLI" --db "$SAFETY_DB" register \
  --title "Active migration guard" \
  --role backend \
  --objective "Keep schema migration away from active work." \
  --exit-criteria "Migration is rejected while work is active." | awk '{print $1}')"
"$CLI" --db "$SAFETY_DB" claim "$SAFETY_JOB" --role backend --claimed-by migration-guard >/dev/null
python3 - "$SAFETY_DB" <<'PY'
import sqlite3
import sys

with sqlite3.connect(sys.argv[1]) as con:
    con.execute("delete from schema_migrations where version = 10")
    con.execute("drop table handoff_notifications")
    con.execute("drop table agent_sessions")
PY
if "$CLI" --db "$SAFETY_DB" migrate >/dev/null 2>&1; then
  echo "ERROR: migration accepted an in-progress handoff" >&2
  exit 1
fi
python3 - "$SAFETY_DB" "$SAFETY_JOB" <<'PY'
import sqlite3
import sys

with sqlite3.connect(sys.argv[1]) as con:
    con.execute("update handoff_jobs set status = 'cancel_requested' where job_id = ?", (sys.argv[2],))
PY
if "$CLI" --db "$SAFETY_DB" migrate >/dev/null 2>&1; then
  echo "ERROR: migration accepted a cancel-requested handoff" >&2
  exit 1
fi
python3 - "$SAFETY_DB" "$SAFETY_JOB" <<'PY'
import sqlite3
import sys

with sqlite3.connect(sys.argv[1]) as con:
    con.execute("update handoff_jobs set status = 'cancelled' where job_id = ?", (sys.argv[2],))
    con.execute(
        """
        insert into waiter_leases(waiter_id, wait_kind, role_id, started_at, heartbeat_at, lease_expires_at)
        values ('migration-waiter', 'watch', 'planning', 'now', 'now', '9999-01-01 00:00:00.000000 UTC')
        """
    )
PY
if "$CLI" --db "$SAFETY_DB" migrate >/dev/null 2>&1; then
  echo "ERROR: migration accepted an active waiter" >&2
  exit 1
fi
python3 - "$SAFETY_DB" <<'PY'
import sqlite3
import sys

with sqlite3.connect(sys.argv[1]) as con:
    con.execute("delete from waiter_leases")
PY
"$CLI" --db "$SAFETY_DB" migrate | grep 'schema=9->10 applied=10:opt_in_thread_notifications' >/dev/null

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
if "$CLI" --db "$AUTO_DB" status >/dev/null 2>&1; then
  echo "ERROR: normal command silently migrated an outdated database" >&2
  exit 1
fi
python3 - "$AUTO_DB" <<'PY'
import sqlite3
import sys

with sqlite3.connect(sys.argv[1]) as con:
    exists = con.execute(
        "select 1 from sqlite_master where type = 'table' and name = 'schema_migrations'"
    ).fetchone()
if exists:
    raise SystemExit("normal command recreated the migration table")
PY
"$CLI" --db "$AUTO_DB" migrate | grep 'schema=0->10 applied=1:initial_schema,2:handoff_cancel_permission,3:named_gates,4:waiter_leases,5:database_metadata,6:workspace_provenance,7:handoff_failures,8:cr_body_integrity,9:plan_revision_controls,10:opt_in_thread_notifications' >/dev/null
test "$(find "$TMP/backups" -type f -name '*.sqlite3' | wc -l | tr -d ' ')" -ge 1

UPGRADE_DB="$TMP/upgrade-v030.sqlite3"
"$CLI" --db "$UPGRADE_DB" init >/dev/null
python3 - "$UPGRADE_DB" <<'PY'
import sqlite3
import sys

with sqlite3.connect(sys.argv[1]) as con:
    con.execute("delete from schema_migrations where version in (3, 4, 5, 6, 7, 8, 9, 10)")
    con.execute("drop table handoff_notifications")
    con.execute("drop table agent_sessions")
    con.execute("drop table handoff_failure_reviews")
    con.execute("drop table workspace_events")
    con.execute("drop table waiter_leases")
    con.execute("drop table database_metadata")
    con.execute("drop table gate_events")
    con.execute("drop table handoff_gate_dependencies")
    con.execute("drop table gate_owners")
    con.execute("drop table workflow_gates")
    con.execute(
        "delete from role_permissions where role_id = 'sm' and permission = 'gate.manage'"
    )
    con.execute(
        "delete from role_permissions where role_id = 'sm' and permission = 'cr.approve'"
    )
PY
if "$CLI" --db "$UPGRADE_DB" migrate --check >/dev/null 2>&1; then
  echo "ERROR: migrate --check accepted a pending v0.3.0 database" >&2
  exit 1
fi
"$CLI" --db "$UPGRADE_DB" migrate | grep 'schema=2->10 applied=3:named_gates,4:waiter_leases,5:database_metadata,6:workspace_provenance,7:handoff_failures,8:cr_body_integrity,9:plan_revision_controls,10:opt_in_thread_notifications' >/dev/null
"$CLI" --db "$UPGRADE_DB" role permission-list sm | grep 'handoff.cancel' >/dev/null
"$CLI" --db "$UPGRADE_DB" role permission-list sm | grep 'gate.manage' >/dev/null
"$CLI" --db "$UPGRADE_DB" role permission-list sm | grep 'workspace.override' >/dev/null
if "$CLI" --db "$UPGRADE_DB" role permission-list sm | grep 'cr.approve' >/dev/null; then
  echo "ERROR: migration restored a project-revoked permission" >&2
  exit 1
fi
"$CLI" --db "$UPGRADE_DB" migrate --check | grep 'schema=10' >/dev/null
python3 - "$UPGRADE_DB" "$BATON_VERSION" <<'PY'
import sqlite3
import sys

with sqlite3.connect(sys.argv[1]) as con:
    metadata = dict(con.execute("select key, value from database_metadata"))
if metadata.get("created_with_baton_version") != "unknown":
    raise SystemExit(f"legacy database creation version was guessed: {metadata}")
if metadata.get("last_migrated_with_baton_version") != sys.argv[2]:
    raise SystemExit(f"legacy database migration version missing: {metadata}")
PY

V5_DB="$TMP/upgrade-v5.sqlite3"
"$CLI" --db "$V5_DB" init >/dev/null
V5_JOB="$("$CLI" --db "$V5_DB" register \
  --title "Preserve v5 handoff" \
  --role backend \
  --objective "Upgrade schema v5 without data loss." \
  --exit-criteria "The handoff remains open after migration." | awk '{print $1}')"
python3 - "$V5_DB" <<'PY'
import sqlite3
import sys

with sqlite3.connect(sys.argv[1]) as con:
    con.execute("delete from schema_migrations where version in (6, 7, 8, 9, 10)")
    con.execute("drop table handoff_notifications")
    con.execute("drop table agent_sessions")
    con.execute("drop table handoff_failure_reviews")
    con.execute("drop table workspace_events")
    con.execute(
        "delete from role_permissions where role_id = 'sm' and permission = 'workspace.override'"
    )
PY
if "$CLI" --db "$V5_DB" migrate --check >/dev/null 2>&1; then
  echo "ERROR: migrate --check accepted a pending schema v5 database" >&2
  exit 1
fi
"$CLI" --db "$V5_DB" migrate | grep 'schema=5->10 applied=6:workspace_provenance,7:handoff_failures,8:cr_body_integrity,9:plan_revision_controls,10:opt_in_thread_notifications' >/dev/null
"$CLI" --db "$V5_DB" handoff show "$V5_JOB" | grep 'Preserve v5 handoff' >/dev/null
"$CLI" --db "$V5_DB" role permission-list sm | grep 'workspace.override' >/dev/null

V6_DB="$TMP/upgrade-v6.sqlite3"
"$CLI" --db "$V6_DB" init >/dev/null
"$CLI" --db "$V6_DB" role add legacy-registrar --display-name "Legacy Registrar" >/dev/null
V6_JOB="$("$CLI" --db "$V6_DB" register \
  --title "Preserve v6 handoff" \
  --role legacy-registrar \
  --objective "Upgrade schema v6 without data loss." \
  --exit-criteria "The handoff and implied registration access remain." | awk '{print $1}')"
python3 - "$V6_DB" <<'PY'
import sqlite3
import sys

with sqlite3.connect(sys.argv[1]) as con:
    con.execute("delete from schema_migrations where version in (7, 8, 9, 10)")
    con.execute("drop table handoff_notifications")
    con.execute("drop table agent_sessions")
    con.execute("drop table handoff_failure_reviews")
    con.execute("delete from role_permissions where permission = 'handoff.register'")
    con.execute("delete from role_permissions where role_id = 'planning' and permission = 'cr.approve'")
PY
"$CLI" --db "$V6_DB" migrate | grep 'schema=6->10 applied=7:handoff_failures,8:cr_body_integrity,9:plan_revision_controls,10:opt_in_thread_notifications' >/dev/null
"$CLI" --db "$V6_DB" handoff show "$V6_JOB" | grep 'Preserve v6 handoff' >/dev/null
"$CLI" --db "$V6_DB" role permission-list legacy-registrar | grep 'handoff.register' >/dev/null
if "$CLI" --db "$V6_DB" role permission-list planning | grep 'cr.approve' >/dev/null; then
  echo "ERROR: schema v7 restored a previously revoked planning CR permission" >&2
  exit 1
fi
"$CLI" --db "$V6_DB" claim "$V6_JOB" --role legacy-registrar >/dev/null
"$CLI" --db "$V6_DB" fail "$V6_JOB" \
  --role legacy-registrar \
  --dir "$TMP/v6-failure-crs" \
  --reason "Verify reviewer fallback." | grep 'reviewer=sm' >/dev/null

V7_DB="$TMP/upgrade-v7.sqlite3"
"$CLI" --db "$V7_DB" init >/dev/null
V7_CR="$("$CLI" --db "$V7_DB" cr create \
  --title "Legacy approved CR" \
  --author-role planning \
  --reviewer-role sm \
  --dir "$TMP/v7-crs" | awk '{print $1}')"
"$CLI" --db "$V7_DB" cr submit "$V7_CR" --role planning >/dev/null
"$CLI" --db "$V7_DB" cr approve "$V7_CR" --role sm >/dev/null
python3 - "$V7_DB" <<'PY'
import sqlite3
import sys

with sqlite3.connect(sys.argv[1]) as con:
    con.execute("delete from schema_migrations where version in (8, 9, 10)")
    con.execute("drop table handoff_notifications")
    con.execute("drop table agent_sessions")
    con.execute("alter table change_requests drop column approved_body_hash")
    con.execute("alter table change_requests drop column submitted_body_hash")
PY
if "$CLI" --db "$V7_DB" migrate --check >/dev/null 2>&1; then
  echo "ERROR: migrate --check accepted a pending schema v7 database" >&2
  exit 1
fi
"$CLI" --db "$V7_DB" migrate | grep 'schema=7->10 applied=8:cr_body_integrity,9:plan_revision_controls,10:opt_in_thread_notifications' >/dev/null
python3 - "$V7_DB" <<'PY'
import sqlite3
import sys

with sqlite3.connect(sys.argv[1]) as con:
    columns = {row[1] for row in con.execute("pragma table_info(change_requests)")}
if not {"submitted_body_hash", "approved_body_hash"} <= columns:
    raise SystemExit(f"schema v8 CR body hash columns are missing: {sorted(columns)}")
PY
"$CLI" --db "$V7_DB" cr status "$V7_CR" | grep 'body_integrity: legacy-unsealed' >/dev/null
"$CLI" --db "$V7_DB" cr seal "$V7_CR" \
  --role sm \
  --evidence "Verified migrated approved CR body." >/dev/null
"$CLI" --db "$V7_DB" cr status "$V7_CR" | grep 'body_integrity: ok' >/dev/null

RACE_DB="$TMP/race.sqlite3"
"$CLI" --db "$RACE_DB" init >/dev/null
RACE_BACKUPS_BEFORE="$(find "$TMP/backups" -type f -name '*.sqlite3' | wc -l | tr -d ' ')"
python3 - "$ROOT/src" "$RACE_DB" <<'PY'
import sqlite3
import sys
from argparse import Namespace

sys.path.insert(0, sys.argv[1])

import agents_baton.cli as cli

db = sys.argv[2]
with sqlite3.connect(db) as con:
    con.execute("delete from schema_migrations where version in (5, 6, 7, 8, 9, 10)")
    con.execute("drop table handoff_notifications")
    con.execute("drop table agent_sessions")
    con.execute("drop table handoff_failure_reviews")
    con.execute("drop table workspace_events")
    con.execute("drop table database_metadata")

original_backup = cli.backup_database

def racing_backup(source, backup_path, expected_signature=""):
    original_backup(source, backup_path, expected_signature)
    with sqlite3.connect(source) as con:
        con.execute("create table migration_race_marker (value text)")

cli.backup_database = racing_backup
try:
    cli.command_migrate(Namespace(db=db, check=False))
except cli.MigrationError as exc:
    if "changed while migration was starting" not in str(exc):
        raise
else:
    raise SystemExit("migration ignored a database change after backup")

with sqlite3.connect(db) as con:
    version = con.execute("select max(version) from schema_migrations").fetchone()[0]
    metadata_table = con.execute(
        "select 1 from sqlite_master where type = 'table' and name = 'database_metadata'"
    ).fetchone()
if version != 4 or metadata_table:
    raise SystemExit("migration race guard changed the schema")
PY
RACE_BACKUPS_AFTER="$(find "$TMP/backups" -type f -name '*.sqlite3' | wc -l | tr -d ' ')"
if [[ "$RACE_BACKUPS_AFTER" != "$RACE_BACKUPS_BEFORE" ]]; then
  echo "ERROR: stale race backup was retained" >&2
  exit 1
fi

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
