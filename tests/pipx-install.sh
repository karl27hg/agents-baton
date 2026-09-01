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

"$PIPX_COMMAND" uninstall agents-baton
test ! -e "$PIPX_BIN_DIR/baton"
test ! -e "$PIPX_BIN_DIR/baton-report"

echo "OK pipx install consumer=$CONSUMER version=$ACTUAL_VERSION"
