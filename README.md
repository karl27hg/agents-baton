# Baton

Baton is a SQLite-backed CLI for coordinating role handoffs, CR review, bounded waits, and shift controls between Codex app agents working in the same repository.

It is designed for use in the Codex app, where multiple role-oriented agents may need a shared local workflow state while working in the same repository.

The goal is to provide transaction-safe handoff operations, role configuration, dependency promotion, event logging, and bounded wait behavior without requiring agents to edit workflow state directly.

## Requirements

Minimum runtime requirements:

- Python 3.10 or newer
- Python standard library `sqlite3` support
- A local filesystem path where Baton can create `.baton/` runtime state

Supported operating systems:

- macOS and Linux are the intended targets.
- Other Unix-like systems should work if Python 3.10+ and SQLite support are available.
- Windows is not currently tested.

No third-party Python packages are required.

The shell tests require additional Unix command-line tools:

- `bash`
- `mktemp`
- `awk`
- `grep`
- `sed`
- `sleep`

This repository is currently tested on macOS with Python 3.12.

## Quick Start

```bash
bin/baton --version
bin/baton init
bin/baton migrate --check
bin/baton role list
bin/baton status
```

## SM Agent Reading Path

An SM/system-manager agent should read these documents in order before configuring Baton for a project:

1. `README.md`: project overview, default roles, CR permissions, wait/shift controls, reports, and GitHub issue wrapper.
2. `docs/using-baton-in-projects.md`: install Baton into another repository, set project `.gitignore`, add `AGENTS.md` rules, and copy role-agent prompts.
3. `docs/gates.md`: named Gate ownership, release, cancellation, emergency transfer, audit, upgrade, and safety rules.
4. `docs/agent-prompt.md`: prompt content to attach to Codex role agents that wait for handoff or CR review work.
5. `docs/agent-usage.md`: command examples for role setup, CR review, waits, shifts, and stop/resume operations.
6. `docs/schema.md` or `docs/schema-ko.md`: database schema and audit table reference when troubleshooting or reviewing workflow state.

For a new database:

```bash
bin/baton init
bin/baton migrate --check
```

For an existing database after updating Baton:

```bash
bin/baton migrate
bin/baton migrate --check
```

Then verify the project configuration:

```bash
bin/baton role list
bin/baton role permission-list sm
bin/baton-report summary
```

Then:

- Map the project's actual role names to Baton roles. Add missing roles with `role add` or aliases with `role alias-add`.
- Decide which roles may review CRs. Grant `cr.review` plus action-specific permissions with `role permission-add`.
- Add `docs/agent-prompt.md` to each Codex role agent's instructions, adjusted for that role and command path.
- Configure shifts and bounded waits using the rules in the Wait and Shift Controls sections below.
- If GitHub issues are used, configure `scripts/gh-repo` with a repo-limited token as described in the GitHub Issue Wrapper section.
- Use `bin/baton-report audit` and `bin/baton-report summary` for read-only operational review.

Agent prompt:

```text
docs/agent-prompt.md
```

When running Baton with Codex role agents, add the agent prompt from `docs/agent-prompt.md` to the worker agent instructions. It defines the bounded wait loop, shift handling, claim/finish rules, and CR review behavior that agents are expected to follow.

Schema reference:

```text
docs/schema.md
docs/schema-ko.md
```

Using Baton in another project:

```text
docs/using-baton-in-projects.md
```

That guide includes copyable prompts for asking Codex role agents to receive handoff work, review CRs, and process CR revision handoffs through Baton.

Release notes:

```text
CHANGELOG.md
```

The default database is:

```text
.baton/baton.sqlite3
```

Use a different database with `--db`:

```bash
bin/baton --db /tmp/baton.sqlite3 init
```

## Role Management

Default roles are seeded by `init`:

```text
sm
planning
architecture
backend
frontend
qa
devops
ui-design
backend-design
```

Add a temporary role:

```bash
bin/baton role add content-design --display-name "Content Design"
```

Add an alias:

```bash
bin/baton role alias-add fe frontend
bin/baton next --role fe
```

Workflow permissions are stored separately from role membership. `sm` is seeded with all CR permissions, `handoff.cancel`, and emergency `gate.manage` authority.

```bash
bin/baton role permission-list sm
bin/baton role permission-add architecture cr.review
bin/baton role permission-add architecture cr.approve
bin/baton role permission-add architecture cr.admin
bin/baton role permission-add architecture handoff.cancel
bin/baton role permission-add architecture gate.manage
bin/baton role permission-remove sm cr.approve
```

After changing the Baton binary or release tag, run `bin/baton migrate` before starting role agents. Migrations are versioned, transactional, and idempotent: existing handoff, CR, event, control, role, and permission rows are preserved. A failed migration is rolled back.

```bash
bin/baton migrate
bin/baton migrate --check
bin/baton role permission-list sm
```

Every database-backed `baton` command also applies pending migrations before its own operation. The explicit command is still recommended so migration failure is detected before agents start. Project-specific permission removals made with `role permission-remove` are preserved: a later migration adds only permissions introduced by that migration and does not restore the full default set. `baton-report` is read-only and does not migrate the database.

`baton update` remains a deprecated alias for database migration for compatibility with v0.1.6. Use `migrate`; the `update` name is reserved for a future Baton binary update workflow.

Use `bin/baton --version` to inspect the installed CLI version. `migrate --check` performs a read-only compatibility check and exits unsuccessfully when migrations are pending or the database is incompatible.

## Agent Identity

Use a stable profile name as the primary agent identity.

```bash
bin/baton agent init --role frontend --agent-id frontend-main
bin/baton claim HO-YYYY-MM-DD-001 --role frontend
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

Profile names are intentionally human-assigned. Do not rely on Codex thread IDs, turn IDs, or temporary files as the only long-lived agent identity. Runtime IDs can be appended in logs when available, but the profile name should remain stable across app restarts and context compaction.

Identity resolution order for `claim`:

1. `--claimed-by`
2. `BATON_AGENT_ID`
3. `--agent-id-file` or `BATON_AGENT_ID_FILE`
4. role name

Use `agent init` only as a local convenience for storing the selected profile name:

```bash
bin/baton agent init --role frontend --agent-id frontend-main
bin/baton agent show
```

The default identity file is ignored by git:

```text
.baton/agent-id
```

If multiple agents share one workspace, do not let them share the same default identity file unless they intentionally represent the same profile. In that case, use `--claimed-by` or `BATON_AGENT_ID` with the assigned profile name for each agent.

## Handoff Flow

Register a ready handoff:

```bash
bin/baton register \
  --title "Frontend upload follow-up" \
  --role frontend \
  --source-ref "docs/change-requests/CR-YYYY-MM-DD-example.md" \
  --objective "Implement the approved upload follow-up." \
  --exit-criteria "The approved UI behavior is implemented and verified."
```

Register a dependent handoff:

```bash
bin/baton register \
  --title "QA regression" \
  --role qa \
  --depends-on HO-YYYY-MM-DD-001 \
  --objective "Verify the completed implementation." \
  --exit-criteria "QA evidence is recorded."
```

Promote ready blocked work:

```bash
bin/baton promote-ready
```

Claim and finish:

```bash
bin/baton next --role frontend
bin/baton claim HO-YYYY-MM-DD-001 --role frontend
bin/baton finish HO-YYYY-MM-DD-001 --role frontend --evidence "Manual verification passed."
```

Inspect events:

```bash
bin/baton events HO-YYYY-MM-DD-001
```

Cancel one handoff with a role that has `handoff.cancel`:

```bash
bin/baton cancel HO-YYYY-MM-DD-001 \
  --role sm \
  --reason "Work is no longer required."
```

Cancellation is scoped. Baton cancels the selected handoff and recursively cancels only `blocked` handoffs that depend on it. Independent `open`, `blocked`, or `in_progress` jobs in other queue branches are unchanged. It does not stop wait loops or clear a role queue; use `stop` for wait control. Finished and already-cancelled handoffs cannot be cancelled again.

## Named Gates

Create a Gate before the final dynamic predecessor handoff exists, then register downstream work against its stable name:

```bash
bin/baton gate create planning-triage-complete --role planning

bin/baton register \
  --title "QA after planning triage" \
  --role qa \
  --depends-on-gate planning-triage-complete \
  --objective "Verify the finalized implementation scope." \
  --exit-criteria "QA evidence covers the planning-approved scope."
```

The creating role is the default owner. Repeat `--owner-role` during creation for joint ownership:

```bash
bin/baton gate create review-complete \
  --role planning \
  --owner-role planning \
  --owner-role architecture
```

An owner releases or cancels a pending Gate. Release evidence is required and eligible blocked handoffs are promoted transactionally.

```bash
bin/baton gate release planning-triage-complete \
  --role planning \
  --evidence "Planning triage approved the final scope."

bin/baton gate cancel obsolete-phase \
  --role planning \
  --reason "The phase was removed from the workflow."
```

Gate cancellation recursively cancels only blocked handoffs in that Gate's dependency branches. For emergency recovery, a current owner or role with `gate.manage` can replace the owner set with an audited reason:

```bash
bin/baton gate transfer planning-triage-complete \
  --role sm \
  --owner-role architecture \
  --reason "Planning agent is unavailable."
```

Inspect Gate state and audit history with `gate status` and `gate events`. Baton records authority by role; user-level authentication remains outside Baton.

See `docs/gates.md` for the complete operational procedure and cautions, including explicit owner-list replacement, terminal Gate states, cancellation scope, upgrade checks, and the distinction between workflow Gates and execution serialization.

## Change Request Flow

CR Markdown files hold the editable request body. SQLite is the authority for workflow state and Baton keeps only the Markdown frontmatter in sync.

Create and submit a CR:

```bash
bin/baton cr create \
  --title "Upload policy" \
  --author-role planning \
  --reviewer-role sm

bin/baton cr submit CR-YYYY-MM-DD-001 --role planning
```

The author role and reviewer role must be different. Baton rejects self-review CRs before submission so they cannot become stuck in the review queue.

Reviewer roles can wait for submitted CRs:

```bash
bin/baton cr wait-review --role sm --timeout 900 --interval 3
```

If the CR needs more work, request a revision. Baton creates a revision handoff for the author role, and another revision cannot be requested until the CR is resubmitted.

```bash
bin/baton cr request-revision CR-YYYY-MM-DD-001 \
  --role sm \
  --reason "Acceptance criteria is unclear."
```

The author role edits the CR Markdown body, resubmits the CR, then finishes the revision handoff:

```bash
bin/baton cr resubmit CR-YYYY-MM-DD-001 \
  --role planning \
  --evidence "Acceptance criteria clarified."

bin/baton finish HO-YYYY-MM-DD-001 \
  --role planning \
  --evidence "CR resubmitted."
```

Approval and implementation assignment are separate decisions:

```bash
bin/baton cr approve CR-YYYY-MM-DD-001 \
  --role sm \
  --evidence "Ready for implementation."

bin/baton cr create-handoff CR-YYYY-MM-DD-001 \
  --by-role sm \
  --role frontend \
  --title "Implement upload policy UI" \
  --objective "Implement the approved upload policy UI." \
  --exit-criteria "UI behavior matches the approved CR."
```

Mark a CR implemented only after every implementation handoff is finished:

```bash
bin/baton cr mark-implemented CR-YYYY-MM-DD-001 \
  --role sm \
  --evidence "Implementation handoffs finished."
```

Administrative CR remediation requires `cr.admin`:

```bash
bin/baton cr reassign-reviewer CR-YYYY-MM-DD-001 \
  --role sm \
  --reviewer-role architecture \
  --reason "Fix incorrect reviewer assignment."

bin/baton cr cancel CR-YYYY-MM-DD-001 \
  --role sm \
  --reason "Superseded by replacement CR."
```

## Transaction Model

State-changing commands run inside `BEGIN IMMEDIATE` transactions:

- `role add`
- `role alias-add`
- `role permission-add`
- `role permission-remove`
- `migrate`
- `cancel`
- `register`
- `gate create`
- `gate release`
- `gate cancel`
- `gate transfer`
- `claim`
- `finish`
- `promote-ready`
- `stop`
- `resume`
- `shift start`
- `shift extend`
- `shift end`
- `cr create`
- `cr submit`
- `cr resubmit`
- `cr request-revision`
- `cr approve`
- `cr reject`
- `cr reassign-reviewer`
- `cr cancel`
- `cr create-handoff`
- `cr mark-implemented`

This is intended to replace ad-hoc lock files for ID assignment and state transitions.

Read-only commands do not claim ownership:

- `role list`
- `role permission-list`
- `migrate --check`
- `status`
- `next`
- `events`
- `gate status`
- `gate events`
- `control status`
- `shift status`
- `cr status`
- `cr events`

Read-only reporting is handled by `bin/baton-report`:

- `audit`
- `summary`

## Tests

Run smoke tests:

```bash
tests/smoke.sh
tests/concurrent-claim.sh
tests/wait-stop.sh
tests/agent-id.sh
tests/cr-flow.sh
tests/gates.sh
tests/handoff-cancel.sh
tests/handoff-dependencies.sh
tests/migrate.sh
tests/shift.sh
tests/report.sh
tests/update.sh
```

The concurrent claim test starts two separate CLI processes against the same open job and expects exactly one claim to succeed.

## Reports

`bin/baton-report` is a read-only reporting CLI for audit and summary output. It opens the SQLite database in read-only mode and must not change workflow state.

Audit history:

```bash
bin/baton-report audit
bin/baton-report audit --job HO-YYYY-MM-DD-001
bin/baton-report audit --cr CR-YYYY-MM-DD-001
bin/baton-report audit --gate planning-triage-complete
bin/baton-report audit --role frontend
bin/baton-report audit --format json
bin/baton-report audit --format csv
```

Summary:

```bash
bin/baton-report summary
bin/baton-report summary --format json
```

## Wait

`wait` repeatedly promotes ready work and checks the target role queue.
`next` is a single non-blocking queue check; it is not a wait command. Agents must not stop working merely because `next` reports no ready jobs.
No-op polling is silent. `wait` prints only a ready job, an actual promotion/cancellation, timeout, or stop result.

```bash
bin/baton wait --role frontend --timeout 900 --interval 3
```

Default wait settings:

- `--timeout 900`: wait for up to 900 seconds.
- `--interval 3`: poll every 3 seconds. The minimum accepted interval is 1 second.

Exit behavior:

- `0`: ready job found
- `2`: timeout
- `3`: stopped by control flag

Required agent loop:

1. Start or extend the role shift.
2. Run a bounded `wait`.
3. On exit `0`, run `next`, claim the returned job, complete it, and report it with `finish`.
4. On exit `2`, check the shift and immediately start another bounded wait while the shift remains active.
5. On exit `3`, stop waiting until the role is resumed.
6. After `finish`, return to step 2 while the shift remains active.

A blocked handoff is not returned by `next`. `wait` keeps checking required upstream jobs and named Gates, then returns after every requirement is resolved and the handoff is promoted to `open`. If a required upstream handoff or Gate is cancelled, Baton recursively marks that blocked dependency branch as `cancelled`; unrelated branches remain active.

`--timeout 0` means wait forever, but that should be used only in explicit experiments. Normal workers must repeat bounded waits until their shift expires or a stop control is set.

`cr wait-review` uses the same stop/resume controls and exit codes, but checks submitted CRs assigned to the reviewer role instead of handoff jobs.

## Shift Controls

Shift controls define how long a role agent should keep starting new waits or claims. They do not block completion reports for work that was already in progress.

Start or extend a role shift:

```bash
bin/baton shift start --role frontend
bin/baton shift extend --role frontend
```

`shift start` defaults to `4h`. `shift extend` defaults to `1h`. Use `--duration` to override either default.

End a shift explicitly:

```bash
bin/baton shift end --role frontend --reason "End of day"
```

Inspect shift state:

```bash
bin/baton shift status --role frontend
```

When a shift expires, Baton marks the matching control scope stopped. Future `wait`, `cr wait-review`, and `claim` attempts stop or fail, while `finish` and CR reporting commands remain allowed.

## Stop And Resume

Stop and resume control is stored in SQLite, not in flag files.

Stop all waiters:

```bash
bin/baton stop --all --reason "End of day"
```

Stop one role:

```bash
bin/baton stop --role frontend --reason "Pause frontend polling"
```

Resume:

```bash
bin/baton resume --all
bin/baton resume --role frontend
```

Inspect control state:

```bash
bin/baton control status
```

Stop/resume commands do not move jobs or change job status. They only affect future `wait` loop behavior.

`resume` clears a manual stop flag. If the shift deadline has already expired, use `shift extend` or `shift start` before re-entering a wait loop.

`stop` writes the stop flag immediately. A running wait loop exits the next time it checks the flag, so response can be delayed by up to the current `--interval`.

## GitHub Issue Wrapper

This repository includes a repo-local GitHub CLI wrapper:

```bash
scripts/gh-repo
```

The wrapper keeps GitHub CLI authentication separate from the OS-wide `gh` configuration:

```bash
export GH_CONFIG_DIR="$ROOT/.baton/gh/config"
export GH_REPO="karl27hg/agents-baton"
```

Authenticate with a fine-grained token that is limited to this repository:

```bash
scripts/gh-repo auth login --with-token
scripts/gh-repo auth status
scripts/gh-repo issue list
```

Token and auth files are stored under `.baton/gh/config/`, which is ignored by git. Do not put token values in the wrapper script or committed documents.

## Limitations

- It does not import or export Markdown handoff files.
- Handoff claim/finish authorization remains target-role based; administrative cancellation uses the separate `handoff.cancel` permission.
- CR review actions use role permissions, but user-level authentication is outside Baton.
- It is not the active repository handoff workflow.

## License

Baton is licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
