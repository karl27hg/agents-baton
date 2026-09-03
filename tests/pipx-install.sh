#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PIPX_COMMAND="${PIPX_COMMAND:-pipx}"
TMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/baton-pipx.XXXXXX")"
trap 'rm -rf "$TMP_ROOT"' EXIT

if ! command -v "$PIPX_COMMAND" >/dev/null 2>&1; then
  echo "ERROR: pipx command not found: $PIPX_COMMAND" >&2
  exit 1
fi

export PIPX_HOME="$TMP_ROOT/pipx-home"
export PIPX_BIN_DIR="$TMP_ROOT/bin"
export PIPX_MAN_DIR="$TMP_ROOT/man"

mkdir -p "$PIPX_BIN_DIR" "$PIPX_MAN_DIR"
"$PIPX_COMMAND" install "$ROOT"

EXPECTED_VERSION="$("$ROOT/bin/baton" --version)"
ACTUAL_VERSION="$("$PIPX_BIN_DIR/baton" --version)"
test "$ACTUAL_VERSION" = "$EXPECTED_VERSION"
test -x "$PIPX_BIN_DIR/baton-report"
test -d "$PIPX_HOME/venvs/agents-baton"
"$PIPX_BIN_DIR/baton" help project migrate | grep -- '--source-db SOURCE_DB' >/dev/null
test "$("$PIPX_BIN_DIR/baton" guide list)" = $'bootstrap\nworker\nplanner\ngit'
"$PIPX_BIN_DIR/baton" guide show bootstrap | grep '^# Agent Bootstrap: Installed Baton' >/dev/null
"$PIPX_BIN_DIR/baton" guide show git | grep '^# Optional Git Workspace Integration' >/dev/null

CONSUMER="$TMP_ROOT/consumer-project"
mkdir -p "$CONSUMER"
(
  cd "$CONSUMER"
  "$PIPX_BIN_DIR/baton" init
  "$PIPX_BIN_DIR/baton" migrate --check
  "$PIPX_BIN_DIR/baton" project info | grep 'schema_version: 6' >/dev/null
  "$PIPX_BIN_DIR/baton" role add update-sentinel --display-name "Update Sentinel"
  "$PIPX_BIN_DIR/baton" role list >/dev/null
  "$PIPX_BIN_DIR/baton-report" summary >/dev/null
  test -f .baton/baton.sqlite3
  test -f .baton/project.json
)

MIGRATION_CONSUMER="$TMP_ROOT/migration-consumer"
LEGACY_DB="$MIGRATION_CONSUMER/tools/baton/.baton/baton.sqlite3"
mkdir -p "$(dirname "$LEGACY_DB")"
"$PIPX_BIN_DIR/baton" --db "$LEGACY_DB" init >/dev/null
"$PIPX_BIN_DIR/baton" --db "$LEGACY_DB" stop --all --reason "pipx migration test" >/dev/null
MIGRATION_TOKEN="$(
  "$PIPX_BIN_DIR/baton" project migrate --check --project-root "$MIGRATION_CONSUMER" |
    awk -F': ' '$1 == "plan_token" {print $2}'
)"
test -n "$MIGRATION_TOKEN"
"$PIPX_BIN_DIR/baton" project migrate \
  --apply \
  --project-root "$MIGRATION_CONSUMER" \
  --plan-token "$MIGRATION_TOKEN" >/dev/null
test -f "$MIGRATION_CONSUMER/.baton/baton.sqlite3"
test -f "$MIGRATION_CONSUMER/.baton/project.json"
"$PIPX_BIN_DIR/baton" --db "$MIGRATION_CONSUMER/.baton/baton.sqlite3" migrate --check >/dev/null

UPGRADE_SOURCE="$TMP_ROOT/upgrade-source"
mkdir -p "$UPGRADE_SOURCE"
cp "$ROOT/pyproject.toml" "$ROOT/README.md" "$UPGRADE_SOURCE/"
cp -R "$ROOT/src" "$UPGRADE_SOURCE/src"
python3 - "$UPGRADE_SOURCE/src/agents_baton/__init__.py" <<'PY'
from pathlib import Path
import re
import sys

path = Path(sys.argv[1])
content = path.read_text(encoding="utf-8")
updated, count = re.subn(
    r'^__version__ = "[^"]+"$',
    '__version__ = "9999.0.0"',
    content,
    count=1,
    flags=re.MULTILINE,
)
if count != 1:
    raise SystemExit("could not replace test package version")
path.write_text(updated, encoding="utf-8")
PY
"$PIPX_COMMAND" install --force "$UPGRADE_SOURCE"
UPDATED_VERSION="$("$PIPX_BIN_DIR/baton" --version)"
test "$UPDATED_VERSION" = "baton 9999.0.0"
(
  cd "$CONSUMER"
  "$PIPX_BIN_DIR/baton" migrate --check >/dev/null
  "$PIPX_BIN_DIR/baton" role list | grep '^update-sentinel' >/dev/null
  "$PIPX_BIN_DIR/baton" guide show bootstrap | grep '^# Agent Bootstrap: Installed Baton' >/dev/null
)
"$PIPX_BIN_DIR/baton" --db "$MIGRATION_CONSUMER/.baton/baton.sqlite3" migrate --check >/dev/null

"$PIPX_COMMAND" uninstall agents-baton
test ! -e "$PIPX_BIN_DIR/baton"
test ! -e "$PIPX_BIN_DIR/baton-report"
test ! -d "$PIPX_HOME/venvs/agents-baton"
test -f "$CONSUMER/.baton/baton.sqlite3"
test -f "$CONSUMER/.baton/project.json"
test -f "$MIGRATION_CONSUMER/.baton/baton.sqlite3"
test -f "$MIGRATION_CONSUMER/.baton/project.json"
if "$PIPX_COMMAND" list --short | grep -q '^agents-baton '; then
  echo "ERROR: agents-baton remains registered after uninstall" >&2
  exit 1
fi

echo "OK pipx lifecycle consumer=$CONSUMER version=$ACTUAL_VERSION updated=$UPDATED_VERSION db=preserved"
