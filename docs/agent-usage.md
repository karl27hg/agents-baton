# Agent Usage: Baton

English (primary) | [한국어 안내](../README.ko.md)

This document is for agents using or testing Baton.

Do not use a test Baton database to operate a live repository handoff queue unless the user explicitly asks for an experiment.

## Agent Rules

- Use `bin/baton`, not raw SQL, for normal operations.
- Use `--db` with a temporary database when testing.
- Use `docs/agent-prompt.md` when running as a wait worker.
- Use a stable profile name as the primary agent identity.
- Claim only jobs targeted to your role.
- Finish only jobs that you have claimed for your role.
- Record concrete evidence when finishing.
- Do not treat Baton DB state as active project workflow state.

## Minimal Agent Flow

```bash
bin/baton --db /tmp/baton.sqlite3 init
bin/baton --db /tmp/baton.sqlite3 shift status --role frontend
bin/baton --db /tmp/baton.sqlite3 shift start --role frontend
bin/baton --db /tmp/baton.sqlite3 wait --role frontend --timeout 900
bin/baton --db /tmp/baton.sqlite3 next --role frontend
bin/baton --db /tmp/baton.sqlite3 claim HO-YYYY-MM-DD-001 --role frontend
bin/baton --db /tmp/baton.sqlite3 finish HO-YYYY-MM-DD-001 --role frontend --evidence "Evidence summary"
bin/baton --db /tmp/baton.sqlite3 shift status --role frontend
bin/baton --db /tmp/baton.sqlite3 wait --role frontend --timeout 900
```

The final `wait` starts the next work cycle. A timeout is not completion; repeat bounded waits while the shift remains active.

## Optional Git Workspace Checks

When the shared project `baton.toml` enables Git integration, inspect the current source context and handoff baseline with:

```bash
bin/baton workspace check
bin/baton workspace check --job HO-YYYY-MM-DD-001
bin/baton workspace events --job HO-YYYY-MM-DD-001
```

No config means `off`; `provider = "git"` defaults to `warn`. In `strict`, report a mismatch instead of bypassing it. Only a role with `workspace.override` may authorize an intentional transition, and the command must include a concrete `--workspace-reason`. See `docs/git-integration.md` for the full policy and checkout procedure.

With isolated worktrees, set `BATON_DB` to one common control database and
`BATON_WORKSPACE_ROOT` to the current checkout. Do not initialize a DB in each worktree.

## Creating Downstream Work

A role may register downstream work only when it has `handoff.register`, a valid source reference, and no unapproved product-scope expansion. New projects grant this permission to `sm` and `planning`. Schema v7 migration grants it to existing active roles to preserve their previous implicit access; review and revoke those compatibility grants when the project requires centralized planning.

The planning agent must follow `docs/planner-prompt.md` before registering parallel work. Jobs are parallel only when their inputs, write sets, contracts, shared state, and accepted completion order are independent. Unknown independence is a dependency, not permission to run concurrently.

When planning must resume after worker execution, register a planning-role reconciliation handoff with every required worker job in `--depends-on`. Use a Gate when the final predecessor set is not yet known. The follow-up belongs to the role queue and may be claimed by another planning agent, so its stored contract must not rely on private conversation context. A planner/SM with direct design authority may register implementation work without creating a self-reviewed CR; use CR review for proposals from another role or changes requiring independent/user approval.

```bash
bin/baton --db /tmp/baton.sqlite3 register \
  --title "Backend follow-up" \
  --role backend \
  --source-ref "cr:CR-YYYY-MM-DD-example" \
  --objective "Apply the approved API contract." \
  --exit-criteria "Backend behavior matches the approved API contract."
```

Use `--depends-on` for sequential work:

```bash
bin/baton --db /tmp/baton.sqlite3 register \
  --title "QA regression" \
  --role qa \
  --depends-on HO-YYYY-MM-DD-001 \
  --objective "Verify the completed implementation." \
  --exit-criteria "QA evidence is recorded."
```

## Role Configuration

Agents can test role configuration in Baton databases:

```bash
bin/baton --db /tmp/baton.sqlite3 role add content-design --display-name "Content Design"
bin/baton --db /tmp/baton.sqlite3 role alias-add cd content-design
bin/baton --db /tmp/baton.sqlite3 next --role cd
```

Workflow authority is configured with role permissions. `sm` is seeded with all CR permissions, `handoff.cancel`, `handoff.register`, emergency `gate.manage`, and `workspace.override` authority. `planning` is seeded with the CR review, retry registration, and cancellation permissions required for failure decisions.

```bash
bin/baton --db /tmp/baton.sqlite3 role permission-list sm
bin/baton --db /tmp/baton.sqlite3 role permission-add architecture cr.review
bin/baton --db /tmp/baton.sqlite3 role permission-add architecture cr.approve
bin/baton --db /tmp/baton.sqlite3 role permission-add architecture handoff.cancel
bin/baton --db /tmp/baton.sqlite3 role permission-add architecture handoff.register
bin/baton --db /tmp/baton.sqlite3 role permission-add architecture gate.manage
bin/baton --db /tmp/baton.sqlite3 role permission-add architecture workspace.override
bin/baton --db /tmp/baton.sqlite3 role permission-remove sm cr.approve
```

Use `permission-remove` rather than editing SQLite when project policy revokes a seeded permission. Baton preserves that revocation across later migrations unless a migration explicitly introduces that same permission as a new default.

Do not change active project roles based on Baton results without SM/user approval.

## Handoff Failure And Retry

Failure is not completion. A worker that cannot satisfy the claimed handoff's exit criteria reports it with a reason and available evidence:

```bash
bin/baton --db /tmp/baton.sqlite3 fail HO-YYYY-MM-DD-001 \
  --role backend \
  --reason "The approved contract cannot represent the required state." \
  --evidence "Contract validation failed."
```

Baton changes the original job from `in_progress` to `failed`, creates a failure CR authored by the target role, submits it to `planning` by default, and leaves every dependent job `blocked`. A planning failure defaults to reviewer `sm`; an explicit reviewer needs `handoff.register`, `cr.review`, `cr.request_revision`, `cr.approve`, `cr.reject`, and `cr.mark_implemented`.

The reviewer chooses one of these paths:

```bash
# Retry the same logical job and preserve its dependency edges.
bin/baton --db /tmp/baton.sqlite3 cr approve CR-YYYY-MM-DD-001 \
  --role planning --evidence "Retry with the corrected contract."
bin/baton --db /tmp/baton.sqlite3 retry HO-YYYY-MM-DD-001 \
  --role planning --cr-id CR-YYYY-MM-DD-001 \
  --reason "Apply the reviewed correction."

# Abandon the failed branch.
bin/baton --db /tmp/baton.sqlite3 cr reject CR-YYYY-MM-DD-001 \
  --role planning --reason "Do not retry this approach."
bin/baton --db /tmp/baton.sqlite3 cancel HO-YYYY-MM-DD-001 \
  --role planning --reason "Failure review rejected retry."
```

`retry` returns the original job to `open`; the target role must claim it again. Finishing the retry is the only action that can release its blocked dependents. After a successful retry, the reviewer may use `cr mark-implemented` because Baton links the retried job as the failure CR implementation. Cancellation requires the failure CR to be rejected or administratively cancelled first. Administrative `cr cancel` also cancels the linked failed job and its blocked descendants.

## Handoff Cancellation

Only an administrative role with `handoff.cancel` may cancel a handoff:

```bash
bin/baton --db /tmp/baton.sqlite3 cancel HO-YYYY-MM-DD-001 \
  --role sm \
  --reason "Work is no longer required."
```

For `blocked`, `open`, and reviewed `failed` jobs, this immediately cancels the selected handoff and recursively cancels only blocked descendants. An `in_progress` handoff becomes `cancel_requested`. The original claimant must stop before commit or integration and acknowledge with evidence:

```bash
bin/baton --db /tmp/baton.sqlite3 cancel-ack HO-YYYY-MM-DD-001 \
  --role backend \
  --claimed-by backend-main \
  --evidence "Stopped before commit; local changes retained for inspection."
```

The claimant first pauses and inspects `events` for the request reason. If review confirms that the existing work remains valid, an authorized planner/SM may restore the existing claim without a new claim:

```bash
bin/baton --db /tmp/baton.sqlite3 cancel-withdraw HO-YYYY-MM-DD-001 \
  --role sm \
  --reason "The implementation is compatible with the reviewed design."
```

The claimant resumes only after `handoff show` reports `in_progress`. A request tied to a cancelled or superseded CR cannot be withdrawn. If cancellation is confirmed, acknowledgement finalizes it and propagates it to blocked descendants. Use `cancel --force` only when the claimant cannot acknowledge. Unrelated queue branches remain unchanged. `stop` controls wait loops and is not job cancellation.

## Named Gate Flow

Use a named Gate when downstream work must be registered before its final dynamic predecessor exists:

```bash
bin/baton --db /tmp/baton.sqlite3 gate create planning-triage-complete \
  --role planning

bin/baton --db /tmp/baton.sqlite3 register \
  --title "QA after planning triage" \
  --role qa \
  --depends-on-gate planning-triage-complete \
  --objective "Verify the final planning scope." \
  --exit-criteria "QA evidence covers the approved scope."
```

The creator role owns the Gate by default. Repeat `--owner-role` on `gate create` when multiple roles may resolve it. Only an owner may release or cancel a Gate. An owner or a role with `gate.manage` may transfer ownership.

```bash
bin/baton --db /tmp/baton.sqlite3 gate release planning-triage-complete \
  --role planning \
  --evidence "Planning triage completed."

bin/baton --db /tmp/baton.sqlite3 gate transfer planning-triage-complete \
  --role sm \
  --owner-role architecture \
  --reason "Emergency transfer because the owner is unavailable."
```

Use `gate transfer` only for an explicit ownership decision. It replaces the complete owner set and records the actor, old owners, new owners, and reason. Gate cancellation cancels only blocked handoffs that require that Gate and their blocked dependency descendants.

Read `docs/gates.md` before operating Gates in a live project. It is the canonical guide for ownership defaults, joint ownership, emergency transfer, terminal states, cancellation propagation, audit, upgrades, and conflict limitations.

## CR Author Flow

CR Markdown is the editable request body. New CRs default to the branch-independent
`.baton/change-requests/` directory in the Baton control root. Baton owns workflow state,
synchronizes Markdown frontmatter, and hashes the body at submission and approval.

```bash
bin/baton --db /tmp/baton.sqlite3 cr create \
  --title "Upload policy" \
  --author-role planning \
  --reviewer-role sm

bin/baton --db /tmp/baton.sqlite3 cr submit CR-YYYY-MM-DD-001 --role planning
```

Use `cr show` to resolve and read the shared document from any source worktree:

```bash
bin/baton --db /tmp/baton.sqlite3 cr show CR-YYYY-MM-DD-001
```

When a reviewer requests revision, claim the generated handoff, edit the CR Markdown body, resubmit the CR, then finish the handoff.

```bash
bin/baton --db /tmp/baton.sqlite3 handoff show HO-YYYY-MM-DD-001
bin/baton --db /tmp/baton.sqlite3 claim HO-YYYY-MM-DD-001 --role planning
bin/baton --db /tmp/baton.sqlite3 cr resubmit CR-YYYY-MM-DD-001 \
  --role planning \
  --evidence "Acceptance criteria clarified."
bin/baton --db /tmp/baton.sqlite3 finish HO-YYYY-MM-DD-001 \
  --role planning \
  --evidence "CR resubmitted."
```

`finish` does not resubmit a CR. The CR state transition must use `cr resubmit`.

Revision handoffs always return to the CR author role. `cr request-revision --assign-back` may state that same role explicitly, but Baton rejects a different role because only the author may resubmit. The review reason is included in the handoff objective. Baton atomically replaces managed Markdown frontmatter and fails without overwriting the document when it detects a concurrent edit.

If a process crash leaves Markdown frontmatter inconsistent with SQLite, use `cr sync CR-ID` to restore only the managed header from authoritative DB state. It preserves the request body. `cr sync` refuses to hide a changed approved body.

## CR Reviewer Flow

Reviewer roles need `cr.review` plus the action-specific permission such as `cr.approve` or `cr.request_revision`.

The CR author role and reviewer role must be different. If an old CR is stuck because it has the same author and reviewer role, an SM/admin role with `cr.admin` must reassign or cancel it instead of editing SQLite directly.

```bash
bin/baton --db /tmp/baton.sqlite3 cr wait-review --role sm --timeout 900
```

Possible review actions:

```bash
bin/baton --db /tmp/baton.sqlite3 cr request-revision CR-YYYY-MM-DD-001 \
  --role sm \
  --reason "Acceptance criteria is unclear."

bin/baton --db /tmp/baton.sqlite3 cr approve CR-YYYY-MM-DD-001 \
  --role sm \
  --evidence "Ready for implementation."

bin/baton --db /tmp/baton.sqlite3 cr reject CR-YYYY-MM-DD-001 \
  --role sm \
  --reason "Out of scope."
```

Approval fails when the current body differs from the submitted hash. Request revision so
the author can resubmit the exact reviewed body. Do not edit an approved CR. `cr status`
and `cr show` report body integrity; implementation assignment, claim, finish, and final
implementation marking stop on an approved-body mismatch.

An existing approved CR migrated without a hash reports `legacy-unsealed`. Its assigned
reviewer must inspect and seal it before new implementation work:

```bash
bin/baton --db /tmp/baton.sqlite3 cr seal CR-YYYY-MM-DD-001 \
  --role sm \
  --evidence "Verified legacy approved body."
```

Administrative remediation:

```bash
bin/baton --db /tmp/baton.sqlite3 cr reassign-reviewer CR-YYYY-MM-DD-001 \
  --role sm \
  --reviewer-role architecture \
  --reason "Fix incorrect reviewer assignment."

bin/baton --db /tmp/baton.sqlite3 cr cancel CR-YYYY-MM-DD-001 \
  --role sm \
  --reason "The request was withdrawn."

bin/baton --db /tmp/baton.sqlite3 cr supersede CR-YYYY-MM-DD-001 \
  --by CR-YYYY-MM-DD-002 \
  --role sm \
  --reason "The approved contract changed incompatibly."
```

Use `--by-source-ref <immutable-ref>` instead of `--by <new-cr>` when a planner/SM has direct design authority and independent review is not required. `cr cancel` retires linked unfinished implementation work. `cr supersede` preserves the immutable old approval, records the replacement, cancels queued linked work, and requests cooperative cancellation of linked active work. Finished work remains evidence and needs an explicit remediation handoff when the replacement invalidates its result.

Approval does not automatically assign implementation. Use a separate implementation handoff when the reviewer decides work should proceed.

```bash
bin/baton --db /tmp/baton.sqlite3 cr create-handoff CR-YYYY-MM-DD-001 \
  --by-role sm \
  --role frontend \
  --title "Implement upload policy UI" \
  --objective "Implement the approved upload policy UI." \
  --exit-criteria "UI behavior matches the approved CR."

bin/baton --db /tmp/baton.sqlite3 cr mark-implemented CR-YYYY-MM-DD-001 \
  --role sm \
  --evidence "Implementation handoffs finished."
```

## Agent Identity

Use a stable profile name before claiming work:

```bash
bin/baton --db /tmp/baton.sqlite3 agent init --role frontend --agent-id frontend-main
bin/baton --db /tmp/baton.sqlite3 agent show
```

Recommended profile names:

```text
sm
frontend-main
backend-main
qa-regression
ui-design-main
backend-design-main
devops-main
architecture-main
```

Do not rely on Codex thread IDs, turn IDs, or temporary files as the only long-lived identity.

The CLI uses identity in this order when claiming:

1. `--claimed-by`
2. `BATON_AGENT_ID`
3. `--agent-id-file` or `BATON_AGENT_ID_FILE`
4. role name

If multiple agents share one workspace, prefer explicit `--claimed-by <profile-name>` or `BATON_AGENT_ID=<profile-name>` so they do not accidentally share one local identity file.

## Wait Usage

Use bounded waits by default:

```bash
bin/baton --db /tmp/baton.sqlite3 wait --role frontend --timeout 900
bin/baton --db /tmp/baton.sqlite3 cr wait-review --role sm --timeout 900
bin/baton --db /tmp/baton.sqlite3 watch --role planning --timeout 900
```

Use `watch` for planner/SM roles that receive both kinds of work. It checks assigned CR reviews first and then ready handoffs; a role without `cr.review` uses it as a handoff-only wait.

`next` checks the queue once and exits immediately; it does not wait. If no ready handoff exists, run `wait`. A blocked handoff becomes visible after all required upstream handoffs finish and `wait` promotes it to `open`.

No-op polling is silent. CLI output is produced for a ready job, an actual promotion or cancellation, timeout, or stop result. A worker must not relay an ordinary timeout as a user-facing update while its shift remains active.

Avoid `--timeout 0` unless the user explicitly asks for a forever-wait experiment. For normal worker operation, repeat bounded waits while the role shift is active. Exit code `2` is only an internal loop boundary: check the shift and enter another bounded wait without reporting when the state is unchanged. Report once for ready work, claim/finish transitions, stop or shift expiry, errors requiring intervention, or an explicit status request. After finishing a claimed job, re-enter the same wait loop while the shift remains active.

If a required upstream handoff or Gate is cancelled, Baton recursively cancels blocked dependent handoffs in that dependency branch. Independent queue branches remain available. Cancelled handoffs do not become ready and must not be reopened by agents.

`--interval` defaults to `auto`. Automatic mode counts active handoff, CR, and combined watchers in the same database and targets `min(30, 3 * active waiters)` seconds with a small stable jitter. Use `--interval N` only for an explicit fixed override; `N` must be at least 1. Fixed waiters remain part of the active count used by automatic waiters.

## Shift Usage

Inspect the applicable global and role controls before the first long-running worker loop:

```bash
bin/baton --db /tmp/baton.sqlite3 shift status --role frontend
bin/baton --db /tmp/baton.sqlite3 shift start --role frontend
bin/baton --db /tmp/baton.sqlite3 shift extend --role frontend
```

`shift start` defaults to `4h`; `shift extend` defaults to `1h`.

If no applicable shift deadline exists and no applicable scope is stopped or expired, start the default role shift. Preserve an existing active deadline. Do not restart, extend, or resume an expired or stopped scope without explicit user or SM authorization.

Use the global scope for a shared project operating window:

```bash
bin/baton --db /tmp/baton.sqlite3 shift status
bin/baton --db /tmp/baton.sqlite3 shift start --all
bin/baton --db /tmp/baton.sqlite3 shift extend --all
bin/baton --db /tmp/baton.sqlite3 shift end --all --reason "End of day"
```

Global and role scopes are cumulative: either can stop the role, and changing one does not clear the other.

When a shift expires, Baton stops future waits and claims for that scope. Finish already-claimed work normally, then check `shift status` before re-entering a wait loop.

## Stop And Resume

Wait loops stop when SM or the user sets a control flag:

```bash
bin/baton --db /tmp/baton.sqlite3 stop --role frontend --reason "Pause frontend"
bin/baton --db /tmp/baton.sqlite3 stop --all --reason "End of day"
```

Resume before starting new waits:

```bash
bin/baton --db /tmp/baton.sqlite3 resume --role frontend
bin/baton --db /tmp/baton.sqlite3 resume --all
```

Check control state:

```bash
bin/baton --db /tmp/baton.sqlite3 control status
```

Stopping wait loops must not be treated as cancelling handoff jobs. It only controls whether waiting agents should keep polling.

`resume` clears manual stops. If the shift has expired, extend or restart the shift before starting another wait.

`stop` writes the stop flag immediately, but a running wait exits only when it next checks the flag. Automatic mode can take up to about 30 seconds when many waiters share the database.
