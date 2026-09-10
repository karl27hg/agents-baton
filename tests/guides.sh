#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CLI="$ROOT/bin/baton"

EXPECTED=$'bootstrap\nworker\nplanner\ngit\nupgrade\nchangelog'
test "$("$CLI" guide list)" = "$EXPECTED"

"$CLI" guide show bootstrap | cmp - "$ROOT/docs/agent-bootstrap.md"
"$CLI" guide show worker | cmp - "$ROOT/docs/agent-prompt.md"
"$CLI" guide show planner | cmp - "$ROOT/docs/planner-prompt.md"
"$CLI" guide show git | cmp - "$ROOT/docs/git-integration.md"
"$CLI" guide show upgrade | cmp - "$ROOT/docs/upgrade-guide.md"
"$CLI" guide show changelog | cmp - "$ROOT/CHANGELOG.md"
"$CLI" guide show upgrade | grep 'Current Release: 0.6.0' >/dev/null
"$CLI" guide show upgrade | grep 'Project AGENTS.md Review' >/dev/null

echo "OK bundled agent guides"
