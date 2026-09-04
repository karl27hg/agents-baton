#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CLI="$ROOT/bin/baton"
REPORT="$ROOT/bin/baton-report"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/baton-worktree-control.XXXXXX")"
TMP="$(cd "$TMP" && pwd -P)"
trap 'rm -rf "$TMP"' EXIT

CONTROL="$TMP/control"
WORK_A="$TMP/work-a"
WORK_B="$TMP/work-b"
mkdir -p "$CONTROL" "$WORK_A"

"$CLI" init --project-root "$CONTROL" >/dev/null
DB="$CONTROL/.baton/baton.sqlite3"
cat >"$CONTROL/baton.toml" <<'EOF'
[vcs]
provider = git
policy = warn
EOF

git -C "$WORK_A" init -q -b main
git -C "$WORK_A" config user.name "Baton Test"
git -C "$WORK_A" config user.email "baton-test@example.invalid"
printf 'initial\n' >"$WORK_A/tracked.txt"
git -C "$WORK_A" add tracked.txt
git -C "$WORK_A" commit -q -m "initial"
git -C "$WORK_A" worktree add -q -b agent-b "$WORK_B"

CR_LINE="$(
  cd "$WORK_A"
  BATON_DB="$DB" BATON_AGENT_ID=planning-a "$CLI" cr create \
    --title "Shared control CR" \
    --author-role planning \
    --reviewer-role sm
)"
CR_ID="$(awk '{print $1}' <<<"$CR_LINE")"
CR_FILE="$(awk '{print $3}' <<<"$CR_LINE")"
test "$CR_FILE" = "$CONTROL/.baton/change-requests/$CR_ID-shared-control-cr.md"
test -f "$CR_FILE"
test ! -e "$WORK_A/.baton"
test ! -e "$WORK_B/.baton"

(
  cd "$WORK_A"
  BATON_DB="$DB" BATON_AGENT_ID=planning-a "$CLI" cr submit "$CR_ID" --role planning >/dev/null
)
(
  cd "$WORK_B"
  BATON_DB="$DB" BATON_AGENT_ID=sm-b "$CLI" cr show "$CR_ID" |
    grep 'body_integrity: ok' >/dev/null
  BATON_DB="$DB" "$REPORT" summary | grep 'Change Requests:' >/dev/null
)

JOB_ID="$(
  cd "$WORK_A"
  BATON_DB="$DB" BATON_AGENT_ID=planning-a "$CLI" register \
    --title "Shared worktree job" \
    --role backend \
    --objective "Verify a shared control database with isolated Git worktrees." \
    --exit-criteria "The second worktree claims the same handoff." |
    awk '{print $1}'
)"
(
  cd "$CONTROL"
  BATON_DB="$DB" BATON_WORKSPACE_ROOT="$WORK_B" BATON_AGENT_ID=backend-b \
    "$CLI" claim "$JOB_ID" --role backend >/dev/null
)
"$CLI" --db "$DB" handoff show "$JOB_ID" | grep 'claimed_by: backend-b' >/dev/null
"$CLI" --db "$DB" workspace events --job "$JOB_ID" |
  grep $'claimed\twarn\taccepted' >/dev/null

echo "OK shared control root with isolated Git worktrees db=$DB"
