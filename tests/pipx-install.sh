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
test "$("$PIPX_BIN_DIR/baton" guide list)" = $'bootstrap\nworker\nplanner'
"$PIPX_BIN_DIR/baton" guide show bootstrap | grep '^# Agent Bootstrap: Installed Baton' >/dev/null

CONSUMER="$TMP_ROOT/consumer-project"
mkdir -p "$CONSUMER"
(
  cd "$CONSUMER"
  "$PIPX_BIN_DIR/baton" init
  "$PIPX_BIN_DIR/baton" migrate --check
  "$PIPX_BIN_DIR/baton" role list >/dev/null
  "$PIPX_BIN_DIR/baton-report" summary >/dev/null
  test -f .baton/baton.sqlite3
)

MIGRATION_CONSUMER="$TMP_ROOT/migration-consumer"
LEGACY_DB="$MIGRATION_CONSUMER/tools/baton/.baton/baton.sqlite3"
mkdir -p "$(dirname "$LEGACY_DB")"
"$PIPX_BIN_DIR/baton" --db "$LEGACY_DB" init >/dev/null
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
"$PIPX_BIN_DIR/baton" --db "$MIGRATION_CONSUMER/.baton/baton.sqlite3" migrate --check >/dev/null

"$PIPX_COMMAND" uninstall agents-baton
test ! -e "$PIPX_BIN_DIR/baton"
test ! -e "$PIPX_BIN_DIR/baton-report"
test ! -d "$PIPX_HOME/venvs/agents-baton"
test -f "$CONSUMER/.baton/baton.sqlite3"
test -f "$MIGRATION_CONSUMER/.baton/baton.sqlite3"
if "$PIPX_COMMAND" list --short | grep -q '^agents-baton '; then
  echo "ERROR: agents-baton remains registered after uninstall" >&2
  exit 1
fi

echo "OK pipx lifecycle consumer=$CONSUMER version=$ACTUAL_VERSION db=preserved"
