#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CLI="$ROOT/bin/baton"
TMP="$(mktemp -d /tmp/baton-multi-project.XXXXXX)"
PROJECT_A="$TMP/project-a"
PROJECT_B="$TMP/project-b"

mkdir -p "$PROJECT_A/work" "$PROJECT_B/work"

(cd "$PROJECT_A" && "$CLI" init >/dev/null)
(cd "$PROJECT_B" && "$CLI" init >/dev/null)

JOB_A="$(cd "$PROJECT_A/work" && "$CLI" register \
  --title "Project A work" \
  --role backend \
  --objective "Stay in project A." \
  --exit-criteria "Only project A changes." | awk '{print $1}')"
JOB_B="$(cd "$PROJECT_B/work" && "$CLI" register \
  --title "Project B work" \
  --role backend \
  --objective "Stay in project B." \
  --exit-criteria "Only project B changes." | awk '{print $1}')"

if [[ "$JOB_A" != "$JOB_B" ]]; then
  echo "ERROR: independent projects did not produce independent job sequences" >&2
  exit 1
fi

(cd "$PROJECT_A/work" && "$CLI" stop --all --reason "Project A maintenance" >/dev/null)
(cd "$PROJECT_A/work" && "$CLI" control status | grep '^all' | grep 'stopped' >/dev/null)
if ! (cd "$PROJECT_B/work" && "$CLI" control status | grep 'No control flags.' >/dev/null); then
  echo "ERROR: project A stop state leaked into project B" >&2
  exit 1
fi

set +e
(cd "$PROJECT_A/work" && "$CLI" wait --role backend --timeout 3 --interval 1) >"$TMP/a-wait.out" 2>&1 &
WAIT_A_PID="$!"
(cd "$PROJECT_B/work" && "$CLI" wait --role frontend --timeout 2 --interval 1) >"$TMP/b-wait.out" 2>&1 &
WAIT_B_PID="$!"
wait "$WAIT_A_PID"
WAIT_A_STATUS="$?"
wait "$WAIT_B_PID"
WAIT_B_STATUS="$?"
set -e

if [[ "$WAIT_A_STATUS" -ne 3 ]]; then
  echo "ERROR: project A waiter ignored its local stop: $WAIT_A_STATUS" >&2
  cat "$TMP/a-wait.out" >&2
  exit 1
fi
if [[ "$WAIT_B_STATUS" -ne 2 ]]; then
  echo "ERROR: project B waiter did not remain isolated: $WAIT_B_STATUS" >&2
  cat "$TMP/b-wait.out" >&2
  exit 1
fi

(cd "$PROJECT_B/work" && "$CLI" next --role backend | grep "$JOB_B" >/dev/null)
(cd "$PROJECT_A/work" && "$CLI" handoff show "$JOB_A" | grep 'Project A work' >/dev/null)
(cd "$PROJECT_B/work" && "$CLI" handoff show "$JOB_B" | grep 'Project B work' >/dev/null)

test "$PROJECT_A/.baton/baton.sqlite3" -ef "$PROJECT_A/work/../.baton/baton.sqlite3"
if [[ "$PROJECT_A/.baton/baton.sqlite3" -ef "$PROJECT_B/.baton/baton.sqlite3" ]]; then
  echo "ERROR: projects share a database file" >&2
  exit 1
fi

echo "OK multi-project project_a=$PROJECT_A project_b=$PROJECT_B job_id=$JOB_A"
