#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CLI="$ROOT/bin/baton"
REPORT="$ROOT/bin/baton-report"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/baton-rc7-semantics.XXXXXX")"
PROJECT="$TMP/project"
trap 'rm -rf "$TMP"' EXIT

mkdir -p "$PROJECT"
cd "$PROJECT"
"$CLI" init >/dev/null

create_cr() {
  local title="$1"
  "$CLI" cr create \
    --title "$title" \
    --author-role planning \
    --reviewer-role sm \
    | awk '{print $1}'
}

approve_cr() {
  local cr_id="$1"
  "$CLI" cr submit "$cr_id" --role planning >/dev/null
  "$CLI" cr approve "$cr_id" --role sm --evidence "Approved." >/dev/null
}

finish_blocking() {
  local title="$1"
  local source_ref="$2"
  local outcome_cr="${3:-}"
  local job_id
  job_id="$(
    "$CLI" register \
      --title "$title" \
      --role backend \
      --source-ref "$source_ref" \
      --objective "Run a completed blocking validation." \
      --exit-criteria "Record the structured result." \
      | awk '{print $1}'
  )"
  "$CLI" claim "$job_id" --role backend >/dev/null
  if [[ -n "$outcome_cr" ]]; then
    "$CLI" finish "$job_id" --role backend \
      --evidence "Validation completed with a blocking result." \
      --outcome fail --blocking --outcome-cr "$outcome_cr" >/dev/null
  else
    "$CLI" finish "$job_id" --role backend \
      --evidence "Validation completed with a blocking result." \
      --outcome fail --blocking >/dev/null
  fi
  printf '%s\n' "$job_id"
}

NO_CR_JOB="$(finish_blocking "Blocking result without CR" "validation:no-cr")"

OPEN_CR="$(create_cr "Open blocking decision")"
OPEN_JOB="$(finish_blocking "Blocking result with open CR" "cr:$OPEN_CR" "$OPEN_CR")"

TERMINAL_CR="$(create_cr "Rejected blocking decision")"
"$CLI" cr submit "$TERMINAL_CR" --role planning >/dev/null
"$CLI" cr claim-review "$TERMINAL_CR" \
  --role sm --claimed-by sm-reviewer >/dev/null
"$CLI" cr status "$TERMINAL_CR" \
  | grep '^active_review_claimed_by: sm-reviewer$' >/dev/null
"$CLI" cr status "$TERMINAL_CR" \
  | grep '^last_review_claimed_by: sm-reviewer$' >/dev/null
TERMINAL_CR_PATH="$(
  "$CLI" cr status "$TERMINAL_CR" | sed -n '1p' | awk -F'\t' '{print $4}'
)"
grep '^review_claimed_by: sm-reviewer$' "$TERMINAL_CR_PATH" >/dev/null
"$CLI" cr reject "$TERMINAL_CR" \
  --role sm --claimed-by sm-reviewer --reason "Decision rejected." >/dev/null
"$CLI" cr status "$TERMINAL_CR" \
  | grep '^active_review_claimed_by: $' >/dev/null
"$CLI" cr status "$TERMINAL_CR" \
  | grep '^last_review_claimed_by: sm-reviewer$' >/dev/null
grep '^review_claimed_by:$' "$TERMINAL_CR_PATH" >/dev/null
"$CLI" cr list --claimed-by sm-reviewer | grep '^No change requests\.$' >/dev/null
"$CLI" cr list --status rejected --format json >"$TMP/rejected.json"
grep '"review_claimed_by": "sm-reviewer"' "$TMP/rejected.json" >/dev/null
grep '"active_review_claimed_by": ""' "$TMP/rejected.json" >/dev/null
grep '"last_review_claimed_by": "sm-reviewer"' "$TMP/rejected.json" >/dev/null
TERMINAL_JOB="$(
  finish_blocking \
    "Blocking result with terminal unimplemented CR" \
    "cr:$TERMINAL_CR" \
    "$TERMINAL_CR"
)"

IMPLEMENTED_CR="$(create_cr "Implemented blocking remediation")"
approve_cr "$IMPLEMENTED_CR"
IMPLEMENTED_BLOCKING_JOB="$(
  finish_blocking \
    "Blocking result with implemented CR" \
    "cr:$IMPLEMENTED_CR" \
    "$IMPLEMENTED_CR"
)"
IMPLEMENTATION_JOB="$(
  "$CLI" cr create-handoff "$IMPLEMENTED_CR" \
    --by-role sm \
    --role backend \
    --title "Implement blocking remediation" \
    --objective "Implement the reviewed remediation." \
    --exit-criteria "Remediation is complete." \
    | awk '{print $2}'
)"
"$CLI" claim "$IMPLEMENTATION_JOB" --role backend >/dev/null
"$CLI" finish "$IMPLEMENTATION_JOB" --role backend \
  --evidence "Remediation completed." --outcome pass >/dev/null
"$CLI" cr mark-implemented "$IMPLEMENTED_CR" \
  --role sm --evidence "Remediation accepted." >/dev/null

"$CLI" handoff show "$NO_CR_JOB" | grep '^blocking_context: no_outcome_cr$' >/dev/null
"$CLI" handoff show "$OPEN_JOB" | grep '^blocking_context: open_cr$' >/dev/null
"$CLI" handoff show "$OPEN_JOB" | grep '^outcome_cr_status: draft$' >/dev/null
"$CLI" handoff show "$IMPLEMENTED_BLOCKING_JOB" \
  | grep '^blocking_context: implemented_cr$' >/dev/null
"$CLI" handoff show "$IMPLEMENTED_BLOCKING_JOB" \
  | grep '^outcome_cr_status: implemented$' >/dev/null
"$CLI" handoff show "$TERMINAL_JOB" \
  | grep '^blocking_context: terminal_unimplemented_cr$' >/dev/null
"$CLI" handoff show "$TERMINAL_JOB" \
  | grep '^outcome_cr_status: rejected$' >/dev/null

"$CLI" status >"$TMP/status.txt"
grep '^outcome.blocking: 4$' "$TMP/status.txt" >/dev/null
grep '^outcome.blocking_total: 4$' "$TMP/status.txt" >/dev/null
grep '^outcome.blocking_with_open_cr: 1$' "$TMP/status.txt" >/dev/null
grep '^outcome.blocking_with_implemented_cr: 1$' "$TMP/status.txt" >/dev/null
grep '^outcome.blocking_with_terminal_unimplemented_cr: 1$' "$TMP/status.txt" >/dev/null
grep '^outcome.blocking_without_cr: 1$' "$TMP/status.txt" >/dev/null

"$REPORT" summary --format json >"$TMP/summary.json"
python3 -c '
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
assert data["blocking_outcomes"] == 4
assert data["blocking_contexts"] == {
    "total": 4,
    "with_open_cr": 1,
    "with_implemented_cr": 1,
    "with_terminal_unimplemented_cr": 1,
    "without_cr": 1,
}
' "$TMP/summary.json"

ADOPTION_CR="$(create_cr "Adoption display semantics")"
approve_cr "$ADOPTION_CR"
ADOPTION_JOB="$(
  "$CLI" register \
    --title "Existing implementation" \
    --role backend \
    --source-ref "cr:$ADOPTION_CR" \
    --objective "Complete the existing implementation." \
    --exit-criteria "Implementation is complete." \
    | awk '{print $1}'
)"
"$CLI" claim "$ADOPTION_JOB" --role backend >/dev/null
"$CLI" finish "$ADOPTION_JOB" --role backend \
  --evidence "Existing implementation completed." --outcome pass >/dev/null
"$CLI" cr show "$ADOPTION_CR" \
  | grep "implementation_adoption_candidate: $ADOPTION_JOB" >/dev/null
if "$CLI" cr mark-implemented "$ADOPTION_CR" \
  --role sm --evidence "No official link." >"$TMP/no-link.txt" 2>&1; then
  echo "ERROR: CR without official implementation link was closed" >&2
  exit 1
fi
grep "eligible adoption candidates: $ADOPTION_JOB:finished" "$TMP/no-link.txt" >/dev/null
"$CLI" cr link-handoff "$ADOPTION_CR" "$ADOPTION_JOB" \
  --role sm --reason "Adopt the completed implementation." >/dev/null

RELATED_JOB="$(
  "$CLI" register \
    --title "Independent acceptance" \
    --role backend \
    --source-ref "cr:$ADOPTION_CR" \
    --objective "Perform independent acceptance." \
    --exit-criteria "Acceptance evidence is recorded." \
    | awk '{print $1}'
)"
"$CLI" claim "$RELATED_JOB" --role backend >/dev/null
"$CLI" finish "$RELATED_JOB" --role backend \
  --evidence "Independent acceptance completed." --outcome pass >/dev/null
if "$CLI" cr show "$ADOPTION_CR" \
  | grep "implementation_adoption_candidate: $RELATED_JOB" >/dev/null; then
  echo "ERROR: related handoff was shown as an adoption candidate after official linkage" >&2
  exit 1
fi
"$CLI" cr show "$ADOPTION_CR" --include-related-handoffs \
  | grep "related_handoff_unlinked: $RELATED_JOB" >/dev/null
"$CLI" cr mark-implemented "$ADOPTION_CR" \
  --role sm --evidence "Official implementation accepted." >/dev/null
if "$CLI" cr status "$ADOPTION_CR" \
  | grep "implementation_adoption_candidate" >/dev/null; then
  echo "ERROR: implemented CR displayed an adoption candidate" >&2
  exit 1
fi
"$CLI" cr status "$ADOPTION_CR" --include-related-handoffs \
  | grep "related_handoff_unlinked: $RELATED_JOB" >/dev/null

"$CLI" migrate --check | grep 'schema=14' >/dev/null
echo "OK RC7 operational semantics blocking=4 adoption=$ADOPTION_JOB related=$RELATED_JOB"
