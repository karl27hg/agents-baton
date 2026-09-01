#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CLI="$ROOT/bin/baton"

EXPECTED=$'bootstrap\nworker\nplanner'
test "$("$CLI" guide list)" = "$EXPECTED"

"$CLI" guide show bootstrap | cmp - "$ROOT/docs/agent-bootstrap.md"
"$CLI" guide show worker | cmp - "$ROOT/docs/agent-prompt.md"
"$CLI" guide show planner | cmp - "$ROOT/docs/planner-prompt.md"

echo "OK bundled agent guides"
