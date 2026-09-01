#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CLI="$ROOT/bin/baton"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/baton-project-migrate.XXXXXX")"
TMP="$(cd "$TMP" && pwd -P)"
trap 'rm -rf "$TMP"' EXIT

plan_token() {
  awk -F': ' '$1 == "plan_token" {print $2}'
}

LEGACY_PROJECT="$TMP/legacy-project"
LEGACY_DB="$LEGACY_PROJECT/tools/baton/.baton/baton.sqlite3"
TARGET_DB="$LEGACY_PROJECT/.baton/baton.sqlite3"
mkdir -p "$(dirname "$LEGACY_DB")"
"$CLI" --db "$LEGACY_DB" init >/dev/null
"$CLI" --db "$LEGACY_DB" role add migration-custom --display-name "Migration Custom" >/dev/null
JOB_ID="$("$CLI" --db "$LEGACY_DB" register \
  --title "Preserve legacy project data" \
  --role backend \
  --objective "Move the legacy Baton database." \
  --exit-criteria "The target contains this handoff." | awk '{print $1}')"

CHECK_OUTPUT="$("$CLI" project migrate --check --project-root "$LEGACY_PROJECT")"
TOKEN="$(printf '%s\n' "$CHECK_OUTPUT" | plan_token)"
test -n "$TOKEN"
printf '%s\n' "$CHECK_OUTPUT" | grep "source_db: $LEGACY_DB" >/dev/null
printf '%s\n' "$CHECK_OUTPUT" | grep "target_db: $TARGET_DB" >/dev/null
printf '%s\n' "$CHECK_OUTPUT" | grep 'layout_move: yes' >/dev/null
test ! -e "$TARGET_DB"

if "$CLI" project migrate --apply --project-root "$LEGACY_PROJECT" >/dev/null 2>&1; then
  echo "ERROR: migration applied without a plan token" >&2
  exit 1
fi
if "$CLI" project migrate --apply --project-root "$LEGACY_PROJECT" --plan-token wrong >/dev/null 2>&1; then
  echo "ERROR: migration applied with a wrong plan token" >&2
  exit 1
fi
test ! -e "$TARGET_DB"

"$CLI" project migrate --apply --project-root "$LEGACY_PROJECT" --plan-token "$TOKEN" >/dev/null
test -f "$TARGET_DB"
test -f "$LEGACY_DB"
test "$(find "$LEGACY_PROJECT/.baton/backups" -type f -name '*.sqlite3' | wc -l | tr -d ' ')" = "1"
"$CLI" --db "$TARGET_DB" migrate --check | grep 'schema=4' >/dev/null
"$CLI" --db "$TARGET_DB" role list | grep '^migration-custom' >/dev/null
python3 - "$TARGET_DB" "$JOB_ID" <<'PY'
import sqlite3
import sys

with sqlite3.connect(sys.argv[1]) as con:
    row = con.execute(
        "select title from handoff_jobs where job_id = ?", (sys.argv[2],)
    ).fetchone()
if row != ("Preserve legacy project data",):
    raise SystemExit(f"migrated handoff missing or changed: {row}")
PY

EXPLICIT_PROJECT="$TMP/explicit-project"
EXPLICIT_DB="$TMP/existing-baton.sqlite3"
mkdir -p "$EXPLICIT_PROJECT"
"$CLI" --db "$EXPLICIT_DB" init >/dev/null
python3 - "$EXPLICIT_DB" <<'PY'
import sqlite3
import sys

with sqlite3.connect(sys.argv[1]) as con:
    con.execute("delete from schema_migrations where version in (3, 4)")
    con.execute("drop table waiter_leases")
    con.execute("drop table gate_events")
    con.execute("drop table handoff_gate_dependencies")
    con.execute("drop table gate_owners")
    con.execute("drop table workflow_gates")
PY

EXPLICIT_CHECK="$("$CLI" project migrate --check \
  --project-root "$EXPLICIT_PROJECT" \
  --source-db "$EXPLICIT_DB")"
EXPLICIT_TOKEN="$(printf '%s\n' "$EXPLICIT_CHECK" | plan_token)"
printf '%s\n' "$EXPLICIT_CHECK" | grep 'source_schema: 2' >/dev/null
printf '%s\n' "$EXPLICIT_CHECK" | grep 'pending_migrations: 3:named_gates,4:waiter_leases' >/dev/null
test ! -e "$EXPLICIT_PROJECT/.baton/baton.sqlite3"

python3 - "$EXPLICIT_DB" <<'PY'
import sqlite3
import sys

with sqlite3.connect(sys.argv[1]) as con:
    con.execute("create table migration_token_change (value text)")
PY
if "$CLI" project migrate --apply \
  --project-root "$EXPLICIT_PROJECT" \
  --source-db "$EXPLICIT_DB" \
  --plan-token "$EXPLICIT_TOKEN" >/dev/null 2>&1; then
  echo "ERROR: migration applied after the checked source changed" >&2
  exit 1
fi
test ! -e "$EXPLICIT_PROJECT/.baton/baton.sqlite3"

EXPLICIT_TOKEN="$("$CLI" project migrate --check \
  --project-root "$EXPLICIT_PROJECT" \
  --source-db "$EXPLICIT_DB" | plan_token)"
"$CLI" project migrate --apply \
  --project-root "$EXPLICIT_PROJECT" \
  --source-db "$EXPLICIT_DB" \
  --plan-token "$EXPLICIT_TOKEN" >/dev/null
"$CLI" --db "$EXPLICIT_PROJECT/.baton/baton.sqlite3" migrate --check | grep 'schema=4' >/dev/null

IN_PLACE_PROJECT="$TMP/in-place-project"
IN_PLACE_DB="$IN_PLACE_PROJECT/.baton/baton.sqlite3"
"$CLI" --db "$IN_PLACE_DB" init >/dev/null
python3 - "$IN_PLACE_DB" <<'PY'
import sqlite3
import sys

with sqlite3.connect(sys.argv[1]) as con:
    con.execute("delete from schema_migrations where version = 4")
    con.execute("drop table waiter_leases")
PY
IN_PLACE_CHECK="$("$CLI" project migrate --check --project-root "$IN_PLACE_PROJECT")"
IN_PLACE_TOKEN="$(printf '%s\n' "$IN_PLACE_CHECK" | plan_token)"
printf '%s\n' "$IN_PLACE_CHECK" | grep 'layout_move: no' >/dev/null
printf '%s\n' "$IN_PLACE_CHECK" | grep 'pending_migrations: 4:waiter_leases' >/dev/null
"$CLI" project migrate --apply \
  --project-root "$IN_PLACE_PROJECT" \
  --plan-token "$IN_PLACE_TOKEN" >/dev/null
"$CLI" --db "$IN_PLACE_DB" migrate --check | grep 'schema=4' >/dev/null
test "$(find "$IN_PLACE_PROJECT/.baton/backups" -type f -name '*.sqlite3' | wc -l | tr -d ' ')" = "1"

MISSING_PROJECT="$TMP/missing-project"
mkdir -p "$MISSING_PROJECT"
if "$CLI" project migrate --check --project-root "$MISSING_PROJECT" >/dev/null 2>&1; then
  echo "ERROR: migration check accepted a project with no database" >&2
  exit 1
fi

INVALID_DB="$TMP/not-baton.sqlite3"
python3 - "$INVALID_DB" <<'PY'
import sqlite3
import sys

with sqlite3.connect(sys.argv[1]) as con:
    con.execute("create table unrelated (value text)")
PY
if "$CLI" project migrate --check \
  --project-root "$MISSING_PROJECT" \
  --source-db "$INVALID_DB" >/dev/null 2>&1; then
  echo "ERROR: migration check accepted a non-Baton database" >&2
  exit 1
fi

AMBIGUOUS_PROJECT="$TMP/ambiguous-project"
"$CLI" --db "$AMBIGUOUS_PROJECT/.baton/baton.sqlite3" init >/dev/null
"$CLI" --db "$AMBIGUOUS_PROJECT/tools/baton/.baton/baton.sqlite3" init >/dev/null
if "$CLI" project migrate --check --project-root "$AMBIGUOUS_PROJECT" >/dev/null 2>&1; then
  echo "ERROR: migration auto-selected one of multiple databases" >&2
  exit 1
fi
if "$CLI" project migrate --check \
  --project-root "$AMBIGUOUS_PROJECT" \
  --source-db "$AMBIGUOUS_PROJECT/tools/baton/.baton/baton.sqlite3" >/dev/null 2>&1; then
  echo "ERROR: migration accepted a distinct existing target" >&2
  exit 1
fi

WAITER_PROJECT="$TMP/waiter-project"
WAITER_DB="$WAITER_PROJECT/tools/baton/.baton/baton.sqlite3"
"$CLI" --db "$WAITER_DB" init >/dev/null
python3 - "$WAITER_DB" <<'PY'
import sqlite3
import sys

with sqlite3.connect(sys.argv[1]) as con:
    con.execute(
        """
        insert into waiter_leases(
          waiter_id, wait_kind, role_id, started_at, heartbeat_at, lease_expires_at
        ) values ('active-test', 'handoff', 'backend',
                  '2099-01-01 00:00:00 UTC', '2099-01-01 00:00:00 UTC',
                  '2099-01-01 01:00:00 UTC')
        """
    )
PY
WAITER_CHECK="$("$CLI" project migrate --check --project-root "$WAITER_PROJECT")"
WAITER_TOKEN="$(printf '%s\n' "$WAITER_CHECK" | plan_token)"
printf '%s\n' "$WAITER_CHECK" | grep 'active_waiters: 1' >/dev/null
if "$CLI" project migrate --apply \
  --project-root "$WAITER_PROJECT" \
  --plan-token "$WAITER_TOKEN" >/dev/null 2>&1; then
  echo "ERROR: migration applied while a waiter lease was active" >&2
  exit 1
fi
test ! -e "$WAITER_PROJECT/.baton/baton.sqlite3"

echo "OK project migration target=$TARGET_DB explicit=$EXPLICIT_DB"
