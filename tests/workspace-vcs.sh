#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CLI="$ROOT/bin/baton"
REPORT="$ROOT/bin/baton-report"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/baton-workspace-vcs.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT

init_git_project() {
  local project="$1"
  local policy="${2:-}"
  mkdir -p "$project"
  git -C "$project" init -q
  git -C "$project" config user.name "Baton Test"
  git -C "$project" config user.email "baton@example.invalid"
  printf '.baton/\n' >"$project/.gitignore"
  printf 'initial\n' >"$project/source.txt"
  if [[ -n "$policy" ]]; then
    {
      printf '[baton]\n'
      printf 'required_version = ">=0.6.0.dev0,<0.7"\n\n'
      printf '[vcs]\n'
      printf 'provider = "git"\n'
      if [[ "$policy" != "default" ]]; then
        printf 'policy = "%s"\n' "$policy"
      fi
    } >"$project/baton.toml"
  fi
  git -C "$project" add .gitignore source.txt
  if [[ -n "$policy" ]]; then
    git -C "$project" add baton.toml
  fi
  git -C "$project" commit -q -m "initial"
  git -C "$project" branch -M main
  (cd "$project" && "$CLI" init >/dev/null)
}

OFF_PROJECT="$TMP/off"
init_git_project "$OFF_PROJECT"
(
  cd "$OFF_PROJECT"
  "$CLI" workspace check | grep '^policy: off$' >/dev/null
  JOB="$($CLI register \
    --title "Off policy" \
    --role backend \
    --objective "Remain Git independent." \
    --exit-criteria "No workspace event is recorded." | awk '{print $1}')"
  "$CLI" claim "$JOB" --role backend >/dev/null
  "$CLI" finish "$JOB" --role backend --evidence "Off policy finished." >/dev/null
  "$CLI" workspace events | grep '^No workspace events.$' >/dev/null
)

WARN_PROJECT="$TMP/warn"
init_git_project "$WARN_PROJECT" default
(
  cd "$WARN_PROJECT"
  "$CLI" workspace check | grep '^policy: warn$' >/dev/null
  git checkout -q -b feature
  printf 'feature\n' >>source.txt
  git add source.txt
  git commit -q -m "feature"
  JOB="$($CLI register \
    --title "Warn policy" \
    --role backend \
    --objective "Warn on a divergent checkout." \
    --exit-criteria "Claim remains allowed and provenance is audited." | awk '{print $1}')"
  git checkout -q main
  printf 'local change\n' >untracked.txt
  "$CLI" claim "$JOB" --role backend >/dev/null 2>warning.txt
  grep 'WARNING: workspace claimed:' warning.txt >/dev/null
  "$CLI" workspace events --job "$JOB" | grep $'claimed\twarn\twarning' >/dev/null
  "$CLI" workspace events --job "$JOB" | grep 'dirty=1' >/dev/null
  "$CLI" finish "$JOB" --role backend --evidence "Warn policy finished." >/dev/null
  "$REPORT" audit --job "$JOB" | grep $'workspace\t' | grep 'claimed:warning' >/dev/null
)

STRICT_PROJECT="$TMP/strict"
init_git_project "$STRICT_PROJECT" strict
(
  cd "$STRICT_PROJECT"
  git checkout -q -b feature
  printf 'feature\n' >>source.txt
  git add source.txt
  git commit -q -m "feature"
  JOB="$($CLI register \
    --title "Strict policy" \
    --role backend \
    --objective "Block a divergent checkout." \
    --exit-criteria "Only an audited SM override can claim it." | awk '{print $1}')"
  git checkout -q main
  if "$CLI" workspace check --job "$JOB" >/dev/null 2>&1; then
    echo "ERROR: strict workspace check accepted a divergent checkout" >&2
    exit 1
  fi
  if "$CLI" claim "$JOB" --role backend >/dev/null 2>&1; then
    echo "ERROR: strict workspace policy allowed a divergent claim" >&2
    exit 1
  fi
  if "$CLI" claim "$JOB" \
    --role backend \
    --accept-workspace-change \
    --workspace-reason "Intentional branch transfer" >/dev/null 2>&1; then
    echo "ERROR: workspace override succeeded without an authorized role" >&2
    exit 1
  fi
  "$CLI" claim "$JOB" \
    --role backend \
    --accept-workspace-change \
    --workspace-reason "Intentional branch transfer" \
    --workspace-authorized-by-role sm >/dev/null
  "$CLI" workspace events --job "$JOB" | grep $'claimed\tstrict\toverride' >/dev/null
  "$CLI" finish "$JOB" --role backend --evidence "Strict override finished." >/dev/null

  mv baton.toml baton.toml.saved
  if "$CLI" workspace check >/dev/null 2>&1; then
    echo "ERROR: strict workspace policy silently disabled after baton.toml disappeared" >&2
    exit 1
  fi
  mv baton.toml.saved baton.toml

  {
    printf '[baton]\n'
    printf 'required_version = ">=9999"\n\n'
    printf '[vcs]\n'
    printf 'provider = "git"\n'
    printf 'policy = "strict"\n'
  } >baton.toml
  if "$CLI" workspace check >/dev/null 2>&1; then
    echo "ERROR: strict workspace check accepted an incompatible Baton version" >&2
    exit 1
  fi
  if "$CLI" register \
    --title "Incompatible version" \
    --role backend \
    --objective "Reject incompatible Baton versions." \
    --exit-criteria "No handoff is registered." >/dev/null 2>&1; then
    echo "ERROR: strict workspace policy registered work with an incompatible Baton version" >&2
    exit 1
  fi
)

echo "OK workspace VCS policies off=$OFF_PROJECT warn=$WARN_PROJECT strict=$STRICT_PROJECT"
