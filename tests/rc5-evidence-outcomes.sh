#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CLI="$ROOT/bin/baton"
REPORT="$ROOT/bin/baton-report"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/baton-rc5-evidence.XXXXXX")"
PROJECT="$TMP/project"
DB="$PROJECT/.baton/baton.sqlite3"
OLD_DB="$TMP/schema12/.baton/baton.sqlite3"
trap 'rm -rf "$TMP"' EXIT

mkdir -p "$PROJECT"
git -C "$PROJECT" init -q
git -C "$PROJECT" config user.name "Baton Test"
git -C "$PROJECT" config user.email "baton@example.invalid"
printf '.baton/\n' >"$PROJECT/.gitignore"
printf 'initial\n' >"$PROJECT/source.txt"
git -C "$PROJECT" add .gitignore source.txt
git -C "$PROJECT" commit -q -m "initial"
FIRST_COMMIT="$(git -C "$PROJECT" rev-parse HEAD)"
BLOB="$(git -C "$PROJECT" rev-parse HEAD:source.txt)"

(
  cd "$PROJECT"
  "$CLI" init >/dev/null
  "$CLI" project info | grep '^database_schema_current: true$' >/dev/null
  "$CLI" project info | grep '^migration_required: false$' >/dev/null
  "$CLI" project info | grep '^cli_schema_compatible: true$' >/dev/null
  "$CLI" project info | grep '^workflow_commands_ready: true$' >/dev/null

  VALIDATION="$($CLI register \
    --title "Security validation" \
    --role backend \
    --objective "Complete validation and report its result." \
    --exit-criteria "A structured result and evidence are recorded." | awk '{print $1}')"
  TRIAGE="$($CLI register \
    --title "Planner triage" \
    --role planning \
    --depends-on "$VALIDATION" \
    --objective "Triage the completed validation result." \
    --exit-criteria "The blocking result has a disposition." | awk '{print $1}')"
  "$CLI" gate create validation-passed --role planning >/dev/null
  IMPLEMENTATION="$($CLI register \
    --title "Success-only implementation" \
    --role backend \
    --depends-on "$VALIDATION" \
    --depends-on-gate validation-passed \
    --objective "Proceed only after an explicit success decision." \
    --exit-criteria "The gate and lifecycle dependency are satisfied." | awk '{print $1}')"
  "$CLI" claim "$VALIDATION" --role backend --claimed-by backend-main >/dev/null

  if "$CLI" finish "$VALIDATION" --role backend --evidence "Missing commit." \
    --commit 548457c33b8273151026dd46ea3fa1500250b2a0 >/dev/null 2>&1; then
    echo "ERROR: missing commit object was accepted" >&2
    exit 1
  fi
  if "$CLI" finish "$VALIDATION" --role backend --evidence "Blob evidence." \
    --commit "$BLOB" >/dev/null 2>&1; then
    echo "ERROR: blob object was accepted as a commit" >&2
    exit 1
  fi
  "$CLI" handoff show "$VALIDATION" | grep '^status: in_progress$' >/dev/null

  "$CLI" finish "$VALIDATION" \
    --role backend \
    --evidence "Validation completed with a blocking product failure." \
    --commit HEAD \
    --outcome fail \
    --blocking >/dev/null
  "$CLI" handoff show "$VALIDATION" | grep "^related_commit: $FIRST_COMMIT$" >/dev/null
  "$CLI" handoff show "$VALIDATION" | grep '^related_commit_resolution: resolved$' >/dev/null
  "$CLI" handoff show "$VALIDATION" | grep '^completion_outcome: fail$' >/dev/null
  "$CLI" handoff show "$VALIDATION" | grep '^completion_blocking: 1$' >/dev/null
  "$CLI" handoff show "$TRIAGE" | grep '^status: open$' >/dev/null
  "$CLI" handoff show "$IMPLEMENTATION" | grep '^status: blocked$' >/dev/null
  "$CLI" status | grep '^outcome.fail: 1$' >/dev/null
  "$CLI" status | grep '^outcome.blocking: 1$' >/dev/null
  "$CLI" handoff list --outcome fail --blocking yes | grep "$VALIDATION" >/dev/null
  PASSING="$($CLI register \
    --title "Passing validation" \
    --role qa \
    --objective "Produce a passing result." \
    --exit-criteria "Pass is recorded." | awk '{print $1}')"
  "$CLI" claim "$PASSING" --role qa >/dev/null
  "$CLI" finish "$PASSING" --role qa --evidence "Pass." --outcome pass >/dev/null
  "$CLI" handoff show "$PASSING" | grep '^completion_blocking: 0$' >/dev/null
  if "$CLI" handoff list --outcome fail --blocking yes | grep "$PASSING" >/dev/null; then
    echo "outcome and blocking filters returned a passing handoff" >&2
    exit 1
  fi
  "$REPORT" summary | grep '^Completion Outcomes:$' >/dev/null
  "$REPORT" summary | grep '^blocking: 1$' >/dev/null

  printf 'correction\n' >>source.txt
  git add source.txt
  git commit -q -m "correction"
  CORRECTED_COMMIT="$(git rev-parse HEAD)"
  "$CLI" handoff evidence-correct "$VALIDATION" \
    --role planning \
    --commit HEAD \
    --reason "Correct the immutable completion commit evidence." >/dev/null
  "$CLI" handoff show "$VALIDATION" \
    | grep "^effective_related_commit: $CORRECTED_COMMIT$" >/dev/null
  "$CLI" handoff show "$VALIDATION" | grep 'evidence_corrections:.*corrected_commit' >/dev/null
  "$CLI" events "$VALIDATION" | grep 'evidence_corrected.*corrected_commit' >/dev/null
  if "$CLI" handoff evidence-correct "$VALIDATION" \
    --role planning --commit HEAD --reason "No-op correction." >/dev/null 2>&1; then
    echo "ERROR: no-op evidence correction was accepted" >&2
    exit 1
  fi
  if "$CLI" handoff evidence-correct "$VALIDATION" \
    --role backend --commit "$FIRST_COMMIT" --reason "Wrong claimant." >/dev/null 2>&1; then
    echo "ERROR: unauthorized evidence correction was accepted" >&2
    exit 1
  fi

  UNRESOLVED="$($CLI register \
    --title "Cross repository evidence" \
    --role backend \
    --objective "Record explicitly unresolved evidence." \
    --exit-criteria "The override reason is audited." | awk '{print $1}')"
  "$CLI" claim "$UNRESOLVED" --role backend --claimed-by backend-main >/dev/null
  "$CLI" finish "$UNRESOLVED" \
    --role backend \
    --evidence "Commit exists only in another repository." \
    --commit 548457c33b8273151026dd46ea3fa1500250b2a0 \
    --allow-unresolved-commit \
    --unresolved-reason "Cross-repository evidence supplied by the integrator." >/dev/null
  "$CLI" handoff show "$UNRESOLVED" | grep '^related_commit_resolution: unresolved$' >/dev/null
  "$CLI" handoff show "$UNRESOLVED" \
    | grep '^related_commit_resolution_reason: Cross-repository evidence' >/dev/null
)

PYTHONPATH="$ROOT/src" python3 - "$OLD_DB" <<'PY'
import sys
from pathlib import Path

from agents_baton import cli

path = Path(sys.argv[1])
path.parent.mkdir(parents=True)
with cli.connect(str(path), create=True) as con:
    con.execute("PRAGMA foreign_keys = OFF")
    con.execute(
        "create table schema_migrations ("
        "version integer primary key, name text not null, applied_at text not null)"
    )
    for version, name, migration in cli.MIGRATIONS:
        if version > 12:
            break
        migration(con)
        con.execute(
            "insert into schema_migrations(version, name, applied_at) values (?, ?, ?)",
            (version, name, cli.utc_now()),
        )
    cli.seed_roles(con)
    cli.seed_role_permissions(con)
    con.execute(
        """
        insert into handoff_jobs(
          job_id, title, status, target_role, objective, exit_criteria,
          created_at, finished_at, closure_evidence, related_commit
        ) values (
          'HO-2026-01-01-001', 'Legacy completion', 'finished', 'backend',
          'Preserve schema 12 completion.', 'Migration preserves the record.',
          ?, ?, 'Legacy evidence.', 'abc123'
        )
        """,
        (cli.utc_now(), cli.utc_now()),
    )
    con.execute(
        "insert into handoff_controls(scope, stopped, reason, work_until, updated_at) "
        "values ('all', 1, 'RC5 migration test', null, ?)",
        (cli.utc_now(),),
    )
PY

"$CLI" --db "$OLD_DB" project info | grep '^schema_version: 12$' >/dev/null
"$CLI" --db "$OLD_DB" project info | grep '^supported_schema_version: 13$' >/dev/null
"$CLI" --db "$OLD_DB" project info | grep '^database_schema_current: false$' >/dev/null
"$CLI" --db "$OLD_DB" project info | grep '^migration_required: true$' >/dev/null
"$CLI" --db "$OLD_DB" project info | grep '^cli_schema_compatible: true$' >/dev/null
"$CLI" --db "$OLD_DB" project info | grep '^workflow_commands_ready: false$' >/dev/null
"$CLI" --db "$OLD_DB" upgrade preflight | grep '^migration_required: true$' >/dev/null
"$CLI" --db "$OLD_DB" migrate | grep 'schema=12->13 applied=13:completion_evidence' >/dev/null
"$CLI" --db "$OLD_DB" handoff show HO-2026-01-01-001 \
  | grep '^related_commit_resolution: legacy_unchecked$' >/dev/null
"$CLI" --db "$OLD_DB" handoff show HO-2026-01-01-001 \
  | grep '^completion_outcome: unspecified$' >/dev/null
"$CLI" --db "$OLD_DB" role permission-list planning \
  | grep $'^planning\thandoff.evidence_correct$' >/dev/null

echo "OK RC5 evidence, outcomes, compatibility, and schema-12 migration"
