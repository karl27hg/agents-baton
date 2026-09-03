#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CLI="$ROOT/bin/baton"
REPORT="$ROOT/bin/baton-report"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/baton-project-root.XXXXXX")"
TMP="$(cd "$TMP" && pwd -P)"
trap 'rm -rf "$TMP"' EXIT

PROJECT_A="$TMP/project-a"
mkdir -p "$PROJECT_A/nested/work"
(
  cd "$PROJECT_A"
  "$CLI" init >/dev/null
)
(
  cd "$PROJECT_A/nested/work"
  "$CLI" status >/dev/null
  "$CLI" project info | grep "project_root: $PROJECT_A" >/dev/null
  "$CLI" project info | grep 'schema_version: 6' >/dev/null
  "$CLI" project info | grep 'last_migrated_with_baton_version:' >/dev/null
  "$REPORT" summary | grep 'Handoffs:' >/dev/null
  "$CLI" agent init --role planning --agent-id planning-root-test >/dev/null
  CR_LINE="$("$CLI" cr create --title "Root anchored CR" --author-role planning --reviewer-role sm)"
  printf '%s\n' "$CR_LINE" | grep 'docs/change-requests/' >/dev/null
)

test -f "$PROJECT_A/.baton/project.json"
test -f "$PROJECT_A/.baton/baton.sqlite3"
test -f "$PROJECT_A/.baton/agent-id"
test -f "$PROJECT_A/docs/change-requests/CR-"*"-root-anchored-cr.md"
test ! -e "$PROJECT_A/nested/work/.baton"
test ! -e "$PROJECT_A/nested/work/docs/change-requests"

MOVED_PROJECT="$TMP/moved-project"
mv "$PROJECT_A" "$MOVED_PROJECT"
(
  cd "$MOVED_PROJECT/nested/work"
  "$CLI" status >/dev/null
  "$REPORT" summary | grep 'Handoffs:' >/dev/null
)
"$CLI" --db "$MOVED_PROJECT/.baton/baton.sqlite3" project info | \
  grep "project_root: $MOVED_PROJECT" >/dev/null

COPIED_PROJECT="$TMP/copied-project"
cp -R "$MOVED_PROJECT" "$COPIED_PROJECT"
(
  cd "$COPIED_PROJECT/nested/work"
  "$CLI" status >/dev/null
)
if [[ "$MOVED_PROJECT/.baton/baton.sqlite3" -ef "$COPIED_PROJECT/.baton/baton.sqlite3" ]]; then
  echo "ERROR: copied projects share one database file" >&2
  exit 1
fi

PROJECT_B="$TMP/project-b"
mkdir -p "$PROJECT_B/.git" "$PROJECT_B/nested/work"
(
  cd "$PROJECT_B/nested/work"
  "$CLI" init --project-root "$PROJECT_B" >/dev/null
)
test -f "$PROJECT_B/.baton/project.json"
test -f "$PROJECT_B/.baton/baton.sqlite3"
test ! -e "$PROJECT_B/nested/work/.baton"

PROJECT_C="$TMP/project-c"
mkdir -p "$PROJECT_C/.git" "$PROJECT_C/nested/work"
(
  cd "$PROJECT_C/nested/work"
  "$CLI" init >/dev/null
)
test -f "$PROJECT_C/nested/work/.baton/project.json"
test -f "$PROJECT_C/nested/work/.baton/baton.sqlite3"
test ! -e "$PROJECT_C/.baton"

LEGACY_PROJECT="$TMP/legacy-project"
LEGACY_DB="$LEGACY_PROJECT/.baton/baton.sqlite3"
mkdir -p "$LEGACY_PROJECT/nested/work"
"$CLI" --db "$LEGACY_DB" init >/dev/null
mv "$LEGACY_PROJECT/.baton/project.json" "$LEGACY_PROJECT/.baton/project.json.removed"
(
  cd "$LEGACY_PROJECT/nested/work"
  "$CLI" status >/dev/null
  "$CLI" init >/dev/null
)
test -f "$LEGACY_PROJECT/.baton/project.json"

UPGRADE_PROJECT="$TMP/upgrade-project"
UPGRADE_DB="$UPGRADE_PROJECT/.baton/baton.sqlite3"
mkdir -p "$UPGRADE_PROJECT/nested/work"
"$CLI" --db "$UPGRADE_DB" init >/dev/null
mv "$UPGRADE_PROJECT/.baton/project.json" "$UPGRADE_PROJECT/.baton/project.json.removed"
python3 - "$UPGRADE_DB" <<'PY'
import sqlite3
import sys

with sqlite3.connect(sys.argv[1]) as con:
    con.execute("delete from schema_migrations where version in (5, 6)")
    con.execute("drop table workspace_events")
    con.execute("drop table database_metadata")
PY
(
  cd "$UPGRADE_PROJECT/nested/work"
  "$CLI" migrate >/dev/null
  "$CLI" migrate --check >/dev/null
)
test -f "$UPGRADE_PROJECT/.baton/project.json"
test "$(find "$UPGRADE_PROJECT/.baton/backups" -type f -name '*.sqlite3' | wc -l | tr -d ' ')" = "1"

MISSING_PROJECT="$TMP/missing-project"
mkdir -p "$MISSING_PROJECT/nested/work"
if (cd "$MISSING_PROJECT/nested/work" && "$CLI" status >/dev/null 2>&1); then
  echo "ERROR: workflow command accepted a missing project database" >&2
  exit 1
fi
if (cd "$MISSING_PROJECT/nested/work" && "$CLI" agent init --role planning >/dev/null 2>&1); then
  echo "ERROR: agent identity initialized outside a Baton project" >&2
  exit 1
fi
test ! -e "$MISSING_PROJECT/.baton"
test ! -e "$MISSING_PROJECT/nested/work/.baton"

EXTERNAL_ROOT="$TMP/external-root"
EXTERNAL_DB="$TMP/external.sqlite3"
mkdir -p "$EXTERNAL_ROOT"
"$CLI" --db "$EXTERNAL_DB" init >/dev/null
if (cd "$EXTERNAL_ROOT" && "$CLI" --db "$EXTERNAL_DB" cr create \
  --title "Ambiguous external CR" \
  --author-role planning \
  --reviewer-role sm >/dev/null 2>&1); then
  echo "ERROR: external database accepted an ambiguous relative CR path" >&2
  exit 1
fi
test ! -e "$EXTERNAL_ROOT/docs/change-requests"

MALFORMED_PROJECT="$TMP/malformed-project"
mkdir -p "$MALFORMED_PROJECT/.baton/nested"
printf '{not-json\n' >"$MALFORMED_PROJECT/.baton/project.json"
if (cd "$MALFORMED_PROJECT/.baton/nested" && "$CLI" status) >"$TMP/malformed.out" 2>&1; then
  echo "ERROR: command accepted a malformed project marker" >&2
  exit 1
fi
grep 'invalid Baton project marker' "$TMP/malformed.out" >/dev/null
if grep 'Traceback' "$TMP/malformed.out" >/dev/null; then
  echo "ERROR: malformed marker exposed a traceback" >&2
  exit 1
fi

MISSING_DB_PROJECT="$TMP/missing-db-project"
mkdir -p "$MISSING_DB_PROJECT/.baton"
printf '{"database":"baton.sqlite3","format_version":1}\n' > \
  "$MISSING_DB_PROJECT/.baton/project.json"
if (cd "$MISSING_DB_PROJECT" && "$CLI" init) >"$TMP/missing-db.out" 2>&1; then
  echo "ERROR: init replaced a missing database under an existing marker" >&2
  exit 1
fi
grep 'project marker exists but its database is missing' "$TMP/missing-db.out" >/dev/null
test ! -e "$MISSING_DB_PROJECT/.baton/baton.sqlite3"

RACE_PROJECT="$TMP/race-project"
mkdir -p "$RACE_PROJECT"
set +e
(cd "$RACE_PROJECT" && "$CLI" init) >"$TMP/race-init-a.out" 2>&1 &
RACE_A_PID="$!"
(cd "$RACE_PROJECT" && "$CLI" init) >"$TMP/race-init-b.out" 2>&1 &
RACE_B_PID="$!"
wait "$RACE_A_PID"
RACE_A_STATUS="$?"
wait "$RACE_B_PID"
RACE_B_STATUS="$?"
set -e
if [[ "$RACE_A_STATUS" -ne 0 || "$RACE_B_STATUS" -ne 0 ]]; then
  echo "ERROR: concurrent project initialization failed" >&2
  cat "$TMP/race-init-a.out" "$TMP/race-init-b.out" >&2
  exit 1
fi
(cd "$RACE_PROJECT" && "$CLI" migrate --check >/dev/null)
python3 - "$RACE_PROJECT/.baton/project.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as marker:
    payload = json.load(marker)
if payload.get("format_version") != 1 or payload.get("database") != "baton.sqlite3":
    raise SystemExit(f"invalid marker after concurrent init: {payload}")
PY

echo "OK marker discovery, move, copy, adoption, report, explicit-db safety, and concurrent init"
