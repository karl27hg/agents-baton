# Baton

English (primary) | [한국어 안내](README.ko.md)

English documentation is the canonical source for behavior and command semantics. The Korean guide provides an overview and navigation to the canonical documents; when the two differ, follow the English document and the installed CLI help.

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

`pipx` is optional and is used only to install Baton as a user-level command. It is not a Baton runtime dependency.

The shell tests require additional Unix command-line tools:

- `bash`
- `mktemp`
- `awk`
- `grep`
- `sed`
- `sleep`

This repository is currently tested on macOS with Python 3.12.

## Quick Start

From a source checkout:

```bash
bin/baton --version
bin/baton init
bin/baton migrate --check
bin/baton role list
bin/baton status
```

After a pipx installation, run the installed command from the project that Baton should manage:

```bash
cd /path/to/your-project
baton init
baton migrate --check
baton status
```

The current directory determines the default `.baton/baton.sqlite3` location.

## Install With Pipx

Install pipx first if the `pipx` command is not already available.

On macOS with Homebrew:

```bash
brew install pipx
pipx ensurepath
```

On Ubuntu 23.04 or newer:

```bash
sudo apt update
sudo apt install pipx
pipx ensurepath
```

Open a new terminal after `pipx ensurepath`, then verify the installation:

```bash
pipx --version
```

For other Linux distributions or installation methods, follow the [official pipx installation guide](https://pipx.pypa.io/latest/how-to/install-pipx.html). Baton does not require pipx when it is run directly from a source checkout through `bin/baton`.

For local validation from this Baton source checkout, run `pipx install .` in the Baton repository root:

```bash
cd /path/to/agents-baton
pipx install .
baton --version
```

The installation command reads `pyproject.toml`, creates an isolated environment, and exposes `baton` and `baton-report` on the user `PATH`. After installation, leave the Baton repository and run those commands from the project that should own the workflow database.

To test the current unpublished packaging branch directly from GitHub:

```bash
pipx install "git+https://github.com/karl27hg/agents-baton.git@codex/pipx-packaging"
baton --version
```

Once a release containing the packaging metadata is published, install that exact Git tag without cloning it first:

```bash
pipx install "git+https://github.com/karl27hg/agents-baton.git@vX.Y.Z"
```

Install Baton once per operating-system user. Do not run `pipx install` again for every project. Instead, change to each project root and initialize its independent runtime database:

```bash
cd /path/to/project-a
baton init
baton migrate --check

cd /path/to/project-b
baton init
baton migrate --check
```

Both projects use the same installed `baton` executable, while `project-a/.baton/baton.sqlite3` and `project-b/.baton/baton.sqlite3` remain separate. Always run Baton from the intended project root unless an explicit `--db` path is supplied.

Verify the managed installation with:

```bash
pipx list
baton --version
```

## Upgrade Or Remove A Pipx Installation

Check the installed version before changing it:

```bash
pipx list
baton --version
```

For a future package-index installation, upgrade the managed application with:

```bash
pipx upgrade agents-baton
```

For a Git tag installation, explicitly replace it with the selected new tag:

```bash
pipx install --force "git+https://github.com/karl27hg/agents-baton.git@vNEW.VERSION"
baton --version
```

After changing the installed Baton version, enter every active consuming project and apply or verify its database migration before starting agents:

```bash
cd /path/to/your-project
baton migrate
baton migrate --check
```

Baton migrations are transactional and preserve existing workflow data. Avoid downgrading to an older Baton after a schema migration because an older executable may not support the newer database schema. Back up the project `.baton/` directory before a planned rollback or high-risk upgrade.

Pin or unpin a pipx environment when automatic upgrades must be controlled:

```bash
pipx pin agents-baton
pipx unpin agents-baton
```

Remove only the installed CLI and its isolated environment with:

```bash
pipx uninstall agents-baton
```

Uninstall does not delete `.baton/` directories or SQLite databases in consuming projects. Remove project runtime state separately only when its workflow history is intentionally being discarded.

A pipx installation is convenient for one user but does not record the selected Baton version in a consuming repository. Use a release-pinned submodule when the project itself must record and review the tool version.

Pipx installs the CLI only. It does not modify a consuming project's `AGENTS.md` or automatically attach the Baton prompts to Codex agents. Complete the project setup in `docs/using-baton-in-projects.md`, including `.gitignore`, role configuration, `docs/agent-prompt.md`, and `docs/planner-prompt.md`.

## SM Agent Reading Path

An SM/system-manager agent should read these documents in order before configuring Baton for a project:

1. `README.md`: project overview, default roles, CR permissions, wait/shift controls, reports, and GitHub issue wrapper.
2. `docs/using-baton-in-projects.md`: install Baton into another repository, set project `.gitignore`, add `AGENTS.md` rules, and copy role-agent prompts.
3. `docs/gates.md`: named Gate ownership, release, cancellation, emergency transfer, audit, upgrade, and safety rules.
4. `docs/planner-prompt.md`: parallel-safety and dependency policy for agents that decompose or register work.
5. `docs/agent-prompt.md`: prompt content to attach to Codex role agents that wait for handoff or CR review work.
6. `docs/agent-usage.md`: command examples for role setup, CR review, waits, shifts, and stop/resume operations.
7. `docs/schema.md` or its Korean translation, `docs/schema.ko.md`: database schema and audit table reference when troubleshooting or reviewing workflow state.

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
- Add `docs/planner-prompt.md` to planning agents that decompose or register concurrent work.
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
docs/schema.ko.md
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

Before registering concurrent handoffs, the planning agent must follow `docs/planner-prompt.md`. Work is parallel only when inputs, write sets, contracts, shared state, and accepted completion order are independent. Otherwise, register the upstream work first and use `--depends-on`, or use a named Gate when the predecessor is not known yet. Baton enforces declared edges and atomic claims but cannot infer missing dependencies or source-level conflicts.

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
bin/baton cr wait-review --role sm --timeout 900
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
- `wait`
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
- `cr wait-review`

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
tests/auto-interval.sh
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

Run the isolated installation test when `pipx` is available:

```bash
tests/pipx-install.sh
```

It installs the current checkout into a temporary pipx home, operates a separate temporary consumer project, verifies both commands, and uninstalls the package. The test confirms that command links and the isolated environment are removed while the consumer project's database is preserved. It does not modify the user's normal pipx installation.

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
No-op polling is silent. `wait` prints only a ready job, an actual promotion/cancellation, timeout, or stop result. An ordinary timeout is a local bounded-loop result; while the shift remains active, the agent must re-enter wait without relaying the timeout or repeating an unchanged waiting status to the user.

```bash
bin/baton wait --role frontend --timeout 900
```

Omitting `--interval` is equivalent to selecting automatic mode explicitly:

```bash
bin/baton wait --role frontend --timeout 900 --interval auto
```

Default wait settings:

- `--timeout 900`: wait for up to 900 seconds.
- `--interval auto`: the default. The target interval is `min(30, 3 * active waiters)` seconds across handoff and CR waiters sharing the database.
- `--interval N`: optional fixed override. The minimum accepted value is 1 second.

Exit behavior:

- `0`: ready job found
- `2`: timeout
- `3`: stopped by control flag

Required agent loop:

1. Start or extend the role shift.
2. Run a bounded `wait`.
3. On exit `0`, run `next`, claim the returned job, complete it, and report it with `finish`.
4. On exit `2`, check the shift and immediately start another bounded wait without reporting while the shift remains active.
5. On exit `3`, stop waiting until the role is resumed.
6. After `finish`, return to step 2 while the shift remains active.

A blocked handoff is not returned by `next`. `wait` keeps checking required upstream jobs and named Gates, then returns after every requirement is resolved and the handoff is promoted to `open`. If a required upstream handoff or Gate is cancelled, Baton recursively marks that blocked dependency branch as `cancelled`; unrelated branches remain active.

`--timeout 0` means wait forever, but that should be used only in explicit experiments. Normal workers must repeat bounded waits until their shift expires or a stop control is set.

`cr wait-review` uses the same stop/resume controls and exit codes, but checks submitted CRs assigned to the reviewer role instead of handoff jobs.

Agents should report waiting state only when it changes: work becomes ready, claim/finish succeeds, stop or shift expiry occurs, an error needs intervention, or the user explicitly asks for status. Repeated polling and repeated exit `2` timeouts are not progress events.

## Resource Usage

The wait commands use polling with `time.sleep()` between unsuccessful checks. They do not busy-spin:

- `wait` checks stop/shift controls, reconciles dependency and Gate state, and inspects one role queue per polling cycle.
- `cr wait-review` checks stop/shift controls and the assigned review queue per polling cycle.
- Both commands register a heartbeat lease in `waiter_leases`. Automatic waits use 30 seconds; fixed intervals over 25 seconds use `interval + 5` seconds so healthy sleepers remain active. Normal exits remove the lease immediately, and a later heartbeat removes stale leases left by disconnected processes.
- Automatic mode counts all active handoff and CR waiters in the same database, including waiters using a fixed override. Its target interval is 3 seconds per active waiter, capped at 30 seconds, with a small stable jitter to avoid synchronized polling.
- A numeric `--interval N` keeps that process on a fixed interval but does not exclude it from the active count used by automatic waiters.

Automatic mode keeps aggregate idle polling approximately bounded as agent count grows. One active waiter targets 3 seconds; two target 6 seconds each; ten or more target 30 seconds each. An explicit fixed interval is intended for a role with a measured response requirement and can increase aggregate SQLite activity when used by many processes.

Measure the installed environment with an empty queue before changing the default. For example, on macOS:

```bash
/usr/bin/time -l bin/baton wait --role frontend --timeout 30
```

The timeout result is expected. Compare user and system CPU time with elapsed time, and repeat using the intended number of concurrent role agents.

See [Baton v0.5.0 idle wait resource check](docs/benchmarks/v0.5.0-idle-wait-resource.md) for the recorded single- and ten-waiter measurements, interpretation, limitations, and distribution policy. The benchmark is repository evidence and is not published as a separate release asset.

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

`stop` writes the stop flag immediately. A running wait loop exits the next time it checks the flag, so response can be delayed by up to the current fixed interval or about 30 seconds in automatic mode.

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
