#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CLI="$ROOT/bin/baton"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/baton-rc6-linkage.XXXXXX")"
PROJECT="$TMP/project"
trap 'rm -rf "$TMP"' EXIT

mkdir -p "$PROJECT"
git -C "$PROJECT" init -q
git -C "$PROJECT" config user.name "Baton Test"
git -C "$PROJECT" config user.email "baton@example.invalid"
printf '.baton/\n' >"$PROJECT/.gitignore"
printf 'implementation\n' >"$PROJECT/source.txt"
git -C "$PROJECT" add .gitignore source.txt
git -C "$PROJECT" commit -q -m "implementation baseline"

cd "$PROJECT"
"$CLI" init >/dev/null

create_approved_cr() {
  local title="$1"
  local line cr_id
  line="$("$CLI" cr create --title "$title" --author-role planning --reviewer-role sm)"
  cr_id="$(printf '%s\n' "$line" | awk '{print $1}')"
  "$CLI" cr submit "$cr_id" --role planning >/dev/null
  "$CLI" cr approve "$cr_id" --role sm --evidence "Approved for RC6 linkage testing." >/dev/null
  printf '%s\n' "$cr_id"
}

CR_ID="$(create_approved_cr "Adopt completed implementation")"
JOB_ID="$("$CLI" register \
  --title "Previously completed implementation" \
  --role backend \
  --source-ref "cr:$CR_ID" \
  --objective "Implement the approved CR outside cr create-handoff." \
  --exit-criteria "Implementation and evidence are complete." | awk '{print $1}')"

if "$CLI" cr show "$CR_ID" \
  | grep "implementation_adoption_candidate: $JOB_ID" >/dev/null; then
  echo "ERROR: unfinished handoff was shown as an adoption candidate" >&2
  exit 1
fi
"$CLI" cr show "$CR_ID" --include-related-handoffs \
  | grep "related_handoff_unlinked: $JOB_ID status=open" >/dev/null
if "$CLI" cr link-handoff "$CR_ID" "$JOB_ID" \
  --role sm --reason "Premature adoption." >/dev/null 2>&1; then
  echo "ERROR: non-finished handoff was linked" >&2
  exit 1
fi

"$CLI" claim "$JOB_ID" --role backend --claimed-by backend-main >/dev/null
"$CLI" finish "$JOB_ID" --role backend \
  --evidence "Implementation completed and verified." \
  --outcome pass --commit HEAD >/dev/null
"$CLI" cr show "$CR_ID" \
  | grep "implementation_adoption_candidate: $JOB_ID status=finished" >/dev/null

if "$CLI" cr mark-implemented "$CR_ID" --role sm --evidence "Missing link." \
  >"$TMP/no-link.out" 2>&1; then
  echo "ERROR: CR without an implementation link was closed" >&2
  exit 1
fi
grep "eligible adoption candidates: $JOB_ID:finished" "$TMP/no-link.out" >/dev/null
grep "baton cr link-handoff $CR_ID" "$TMP/no-link.out" >/dev/null

if "$CLI" cr link-handoff "$CR_ID" "$JOB_ID" \
  --role backend --reason "Unauthorized adoption." >/dev/null 2>&1; then
  echo "ERROR: unauthorized role linked an implementation" >&2
  exit 1
fi

"$CLI" cr link-handoff "$CR_ID" "$JOB_ID" \
  --role sm --reason "Implementation completed before structured CR linkage." \
  | grep $'\tlinked\timplementation$' >/dev/null
"$CLI" cr status "$CR_ID" \
  | grep "implementation_handoff: $JOB_ID status=finished outcome=pass blocking=0 commit_resolution=resolved" >/dev/null
if "$CLI" cr status "$CR_ID" | grep "implementation_adoption_candidate: $JOB_ID" >/dev/null; then
  echo "ERROR: linked handoff remains an unlinked candidate" >&2
  exit 1
fi
"$CLI" cr events "$CR_ID" | grep "implementation_handoff_linked.*job=$JOB_ID" >/dev/null
EVENT_COUNT="$("$CLI" cr events "$CR_ID" | grep -c implementation_handoff_linked)"
"$CLI" cr link-handoff "$CR_ID" "$JOB_ID" \
  --role sm --reason "Idempotent retry." \
  | grep $'\talready-linked\timplementation$' >/dev/null
test "$("$CLI" cr events "$CR_ID" | grep -c implementation_handoff_linked)" = "$EVENT_COUNT"
"$CLI" cr mark-implemented "$CR_ID" --role sm \
  --evidence "Adopted implementation verified." >/dev/null

BLOCKING_CR="$(create_approved_cr "Reject blocking implementation closure")"
BLOCKING_JOB="$("$CLI" cr create-handoff "$BLOCKING_CR" \
  --by-role sm --role backend \
  --title "Blocking implementation validation" \
  --objective "Complete implementation validation." \
  --exit-criteria "Record the validation result." | awk '{print $2}')"
"$CLI" claim "$BLOCKING_JOB" --role backend >/dev/null
"$CLI" finish "$BLOCKING_JOB" --role backend \
  --evidence "Validation found a blocking defect." \
  --outcome fail --blocking >/dev/null
if "$CLI" cr mark-implemented "$BLOCKING_CR" --role sm \
  --evidence "Must remain open." >"$TMP/blocking.out" 2>&1; then
  echo "ERROR: blocking implementation result closed its CR" >&2
  exit 1
fi
grep "$BLOCKING_JOB:finished:blocking:fail" "$TMP/blocking.out" >/dev/null
"$CLI" cr status "$BLOCKING_CR" | sed -n '1p' | grep $'\tapproved\t' >/dev/null

MISMATCH_CR="$(create_approved_cr "Reject unrelated source")"
MISMATCH_JOB="$("$CLI" register \
  --title "Unrelated finished handoff" \
  --role backend \
  --source-ref "user:unrelated" \
  --objective "Produce unrelated work." \
  --exit-criteria "Unrelated work is complete." | awk '{print $1}')"
"$CLI" claim "$MISMATCH_JOB" --role backend >/dev/null
"$CLI" finish "$MISMATCH_JOB" --role backend \
  --evidence "Unrelated work completed." --outcome pass >/dev/null
if "$CLI" cr link-handoff "$MISMATCH_CR" "$MISMATCH_JOB" \
  --role sm --reason "Source mismatch." >/dev/null 2>&1; then
  echo "ERROR: mismatched source_ref was linked" >&2
  exit 1
fi

UNRESOLVED_CR="$(create_approved_cr "Verify unresolved legacy evidence")"
UNRESOLVED_JOB="$("$CLI" register \
  --title "External evidence implementation" \
  --role backend \
  --source-ref "cr:$UNRESOLVED_CR" \
  --objective "Record external implementation evidence." \
  --exit-criteria "Implementation evidence is recorded." | awk '{print $1}')"
"$CLI" claim "$UNRESOLVED_JOB" --role backend --claimed-by backend-main >/dev/null
"$CLI" finish "$UNRESOLVED_JOB" --role backend \
  --evidence "External commit reference supplied." --outcome pass \
  --commit 548457c33b8273151026dd46ea3fa1500250b2a0 \
  --allow-unresolved-commit \
  --unresolved-reason "Commit belongs to an external repository." >/dev/null
if "$CLI" cr link-handoff "$UNRESOLVED_CR" "$UNRESOLVED_JOB" \
  --role sm --reason "Unverified evidence." >"$TMP/unresolved.out" 2>&1; then
  echo "ERROR: unresolved commit evidence was linked" >&2
  exit 1
fi
grep "commit evidence is unresolved" "$TMP/unresolved.out" >/dev/null
"$CLI" handoff evidence-correct "$UNRESOLVED_JOB" \
  --role sm --commit HEAD --reason "Verified the implementation against local HEAD." >/dev/null
"$CLI" cr show "$UNRESOLVED_CR" \
  | grep "implementation_adoption_candidate: $UNRESOLVED_JOB.*commit_resolution=resolved" >/dev/null
"$CLI" cr link-handoff "$UNRESOLVED_CR" "$UNRESOLVED_JOB" \
  --role sm --reason "Evidence verified and corrected." >/dev/null

OTHER_CR="$(create_approved_cr "Reject cross-CR implementation reuse")"
if "$CLI" cr link-handoff "$OTHER_CR" "$UNRESOLVED_JOB" \
  --role sm --reason "Cross-CR reuse." >"$TMP/other-cr.out" 2>&1; then
  echo "ERROR: implementation handoff was linked to multiple CRs" >&2
  exit 1
fi
grep "already linked as implementation to another CR: $UNRESOLVED_CR" \
  "$TMP/other-cr.out" >/dev/null

"$CLI" migrate --check | grep 'schema=15' >/dev/null
echo "OK RC6 CR linkage cr=$CR_ID job=$JOB_ID blocking=$BLOCKING_JOB"
