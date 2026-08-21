#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CLI="$ROOT/bin/baton"
TMP="$(mktemp -d /tmp/baton-gates.XXXXXX)"
DB="$TMP/baton.sqlite3"

"$CLI" --db "$DB" init >/dev/null
"$CLI" --db "$DB" role permission-list sm | grep 'gate.manage' >/dev/null

"$CLI" --db "$DB" gate create planning-triage-complete --role planning \
  | grep 'owners=planning' >/dev/null
"$CLI" --db "$DB" gate create shared-review --role planning \
  --owner-role planning --owner-role architecture \
  | grep 'owners=planning,architecture' >/dev/null
if "$CLI" --db "$DB" gate create shared-review --role planning >/dev/null 2>&1; then
  echo "ERROR: duplicate gate name was accepted" >&2
  exit 1
fi

if "$CLI" --db "$DB" register \
  --title "Unknown gate" \
  --role qa \
  --depends-on-gate missing-gate \
  --objective "Must fail." \
  --exit-criteria "The gate exists." >/dev/null 2>&1; then
  echo "ERROR: handoff accepted an unknown gate" >&2
  exit 1
fi

UPSTREAM="$($CLI --db "$DB" register \
  --title "Review consolidation" \
  --role backend \
  --objective "Complete the known review stage." \
  --exit-criteria "Review evidence is recorded." | awk '{print $1}')"
GATED="$($CLI --db "$DB" register \
  --title "Planning-gated QA" \
  --role qa \
  --depends-on "$UPSTREAM" \
  --depends-on-gate planning-triage-complete \
  --objective "Wait for both review and planning." \
  --exit-criteria "Both prerequisites are resolved." | awk '{print $1}')"

"$CLI" --db "$DB" claim "$UPSTREAM" --role backend --claimed-by gate-test >/dev/null
"$CLI" --db "$DB" finish "$UPSTREAM" --role backend --evidence "Review complete." >/dev/null
"$CLI" --db "$DB" promote-ready >/dev/null
if "$CLI" --db "$DB" next --role qa >/dev/null 2>&1; then
  echo "ERROR: pending gate allowed premature promotion" >&2
  exit 1
fi

if "$CLI" --db "$DB" gate release planning-triage-complete \
  --role frontend --evidence "Unauthorized." >/dev/null 2>&1; then
  echo "ERROR: non-owner released a gate" >&2
  exit 1
fi
if "$CLI" --db "$DB" gate release planning-triage-complete \
  --role sm --evidence "Manager bypass." >/dev/null 2>&1; then
  echo "ERROR: gate.manage bypassed owner-only release" >&2
  exit 1
fi
if "$CLI" --db "$DB" gate transfer planning-triage-complete \
  --role frontend --owner-role architecture --reason "Unauthorized." >/dev/null 2>&1; then
  echo "ERROR: non-owner transferred a gate" >&2
  exit 1
fi

"$CLI" --db "$DB" gate transfer planning-triage-complete \
  --role sm \
  --owner-role architecture \
  --owner-role qa \
  --reason "Emergency ownership transfer." \
  | grep 'owners=architecture,qa' >/dev/null
if "$CLI" --db "$DB" gate release planning-triage-complete \
  --role planning --evidence "Former owner." >/dev/null 2>&1; then
  echo "ERROR: former owner released a transferred gate" >&2
  exit 1
fi
UNRELATED_UPSTREAM="$($CLI --db "$DB" register \
  --title "Unrelated release scope" \
  --role backend \
  --objective "Finish without global promotion." \
  --exit-criteria "The job is finished." | awk '{print $1}')"
UNRELATED_BLOCKED="$($CLI --db "$DB" register \
  --title "Unrelated blocked release scope" \
  --role ui-design \
  --depends-on "$UNRELATED_UPSTREAM" \
  --objective "Require explicit global promotion." \
  --exit-criteria "The upstream is finished." | awk '{print $1}')"
"$CLI" --db "$DB" claim "$UNRELATED_UPSTREAM" --role backend --claimed-by gate-test >/dev/null
"$CLI" --db "$DB" finish "$UNRELATED_UPSTREAM" --role backend --evidence "Finished." >/dev/null
"$CLI" --db "$DB" gate release planning-triage-complete \
  --role architecture \
  --evidence "Planning triage approved." \
  | grep 'promoted=1' >/dev/null
"$CLI" --db "$DB" next --role qa | grep "$GATED" >/dev/null
if "$CLI" --db "$DB" next --role ui-design >/dev/null 2>&1; then
  echo "ERROR: gate release promoted an unrelated dependency branch" >&2
  exit 1
fi
"$CLI" --db "$DB" promote-ready >/dev/null
"$CLI" --db "$DB" next --role ui-design | grep "$UNRELATED_BLOCKED" >/dev/null
"$CLI" --db "$DB" gate events planning-triage-complete \
  | grep 'ownership_transferred' >/dev/null
"$CLI" --db "$DB" gate events planning-triage-complete \
  | grep 'released' >/dev/null

RELEASED_DEP="$($CLI --db "$DB" register \
  --title "Already released gate" \
  --role devops \
  --depends-on-gate planning-triage-complete \
  --objective "Open immediately." \
  --exit-criteria "Released gates do not block." | awk '{print $2}')"
if [[ "$RELEASED_DEP" != "open" ]]; then
  echo "ERROR: released gate blocked a new handoff" >&2
  exit 1
fi

"$CLI" --db "$DB" gate create review-part-a --role planning >/dev/null
"$CLI" --db "$DB" gate create review-part-b --role planning >/dev/null
MULTI_GATE="$($CLI --db "$DB" register \
  --title "Multiple gate dependency" \
  --role backend-design \
  --depends-on-gate review-part-a \
  --depends-on-gate review-part-b \
  --objective "Wait for every gate." \
  --exit-criteria "Both gates are released." | awk '{print $1}')"
"$CLI" --db "$DB" gate release review-part-a --role planning --evidence "Part A complete." \
  | grep 'promoted=0' >/dev/null
if "$CLI" --db "$DB" next --role backend-design >/dev/null 2>&1; then
  echo "ERROR: one released gate bypassed another pending gate" >&2
  exit 1
fi
"$CLI" --db "$DB" gate release review-part-b --role planning --evidence "Part B complete." \
  | grep 'promoted=1' >/dev/null
"$CLI" --db "$DB" next --role backend-design | grep "$MULTI_GATE" >/dev/null

"$CLI" --db "$DB" gate create cancelled-phase --role planning >/dev/null
CANCEL_ROOT="$($CLI --db "$DB" register \
  --title "Cancelled gate root" \
  --role backend \
  --depends-on-gate cancelled-phase \
  --objective "Wait for a gate that will be cancelled." \
  --exit-criteria "The gate is released." | awk '{print $1}')"
CANCEL_CHILD="$($CLI --db "$DB" register \
  --title "Cancelled gate child" \
  --role frontend \
  --depends-on "$CANCEL_ROOT" \
  --objective "Wait for the gate root." \
  --exit-criteria "The root is complete." | awk '{print $1}')"
UNRELATED="$($CLI --db "$DB" register \
  --title "Independent frontend work" \
  --role frontend \
  --objective "Remain open after gate cancellation." \
  --exit-criteria "Independent work remains available." | awk '{print $1}')"

"$CLI" --db "$DB" gate cancel cancelled-phase \
  --role planning \
  --reason "Phase is no longer required." \
  | grep 'handoffs=2' >/dev/null
"$CLI" --db "$DB" events "$CANCEL_ROOT" | grep 'gate_cancelled' >/dev/null
"$CLI" --db "$DB" events "$CANCEL_CHILD" | grep 'dependency_cancelled' >/dev/null
"$CLI" --db "$DB" next --role frontend | grep "$UNRELATED" >/dev/null

CANCELLED_NEW="$($CLI --db "$DB" register \
  --title "Late cancelled dependency" \
  --role qa \
  --depends-on-gate cancelled-phase \
  --objective "Start cancelled." \
  --exit-criteria "The gate is available." | awk '{print $1" "$2}')"
if [[ "$CANCELLED_NEW" != HO-*" cancelled" ]]; then
  echo "ERROR: handoff behind an already-cancelled gate did not start cancelled" >&2
  exit 1
fi

echo "OK gates gated=$GATED unrelated=$UNRELATED db=$DB"
