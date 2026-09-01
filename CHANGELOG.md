# Changelog

## Unreleased

- Added Python packaging with `baton` and `baton-report` console entry points for isolated pipx installation.
- Added a temporary-home pipx lifecycle test that verifies command and environment removal while preserving the consumer project's Baton database.

## v0.5.1

- Added reproducible v0.5.0 idle-wait CPU, memory, SQLite growth, lease cleanup, thermal interpretation, and distribution evidence.
- Added a planner policy for dependency-safe parallel handoffs and strengthened worker prompts to suppress ordinary timeout and unchanged-wait reports.

## v0.5.0

- Made automatic polling the default for handoff and CR review waits, targeting three seconds per active waiter with a 30-second cap and stable jitter.
- Added schema migration v4 for short-lived waiter heartbeat leases, including stale lease cleanup and fixed-interval compatibility.
- Bounded polling sleep by the remaining timeout so a scaled interval cannot overrun a short wait deadline.
- Added concurrent waiter, automatic scaling, stale cleanup, fixed override, timeout, migration, and data-preservation coverage.
- Made English the canonical documentation language and added Korean overview and schema navigation.

## v0.4.1

- Added a dedicated named Gate operations guide covering ownership, emergency transfer, cancellation scope, terminal states, audit, upgrades, and execution-conflict limitations.

## v0.4.0

- Added named workflow gates for handoffs that must wait for a future or manually resolved stage.
- Added default creator ownership, optional joint owners, owner release/cancel, and audited emergency ownership transfer through `gate.manage`.
- Added transactional gate release promotion and gate cancellation propagation while preserving unrelated queue branches.
- Added schema migration v3 and read-only gate audit/summary reporting.
- Documented the staged Sol/Tera review workflow and durable Markdown artifact pattern from GitHub issue #3.
- Added gate ownership, migration, promotion, cancellation, reporting, and independent-queue regression coverage.

## v0.3.0

- Added scoped handoff cancellation through `baton cancel` and the `handoff.cancel` role permission.
- Added schema migration v2 to grant `handoff.cancel` to the default `sm` role without changing existing workflow data.
- Added `role permission-remove` and preserved project-specific permission revocations across later migrations.
- Made no-op wait polling silent while preserving ready, transition, timeout, and stop output.
- Added `baton --version`, `baton migrate --check`, and expanded command help.
- Clarified the required agent wait loop, cancellation scope, and install versus upgrade procedures.
- Added regression coverage for cancellation authorization, dependency propagation, unrelated queue preservation, quiet waits, and v0.2.0 database migration.

## v0.2.0

- Added versioned, transactional database migrations through `baton migrate`.
- Preserved `baton update` as a deprecated migration alias for v0.1.6 compatibility.
- Added data-preservation, idempotency, newer-schema rejection, and rollback regression coverage.
- Cancelled blocked handoffs recursively when a required upstream handoff is cancelled.
- Added dependency wait, promotion, and cancellation cascade regression coverage.
- Clarified the required bounded-wait loop for role agents.

## v0.1.6

- Added `baton update` as an explicit idempotent database upgrade command.
- Documented the post-upgrade `baton update` flow for consuming projects.
- Added an update regression test for newly seeded default permissions.

## v0.1.5

- Prevented self-review CRs where `author_role` and `reviewer_role` are the same.
- Added `cr.admin` permission for administrative CR remediation.
- Added `cr reassign-reviewer` and `cr cancel` with audit events and Markdown frontmatter sync.
- Documented the self-review prevention and remediation flow.

## v0.1.4

- Added the read-only `baton-report` CLI for audit and summary output.
- Added an SM agent reading path and setup checklist to the README.

## v0.1.3

- Changed default wait polling interval from 30 seconds to 3 seconds for faster handoff and CR review response.
- Added validation that `--interval` must be at least 1 second.
- Updated wait interval documentation and agent prompts.
- Removed remaining prototype wording from user-facing Baton documentation and CLI help.

## v0.1.2

- Documented how to use Baton from another project.
- Added copyable Codex agent prompts for handoff workers, CR reviewers, and revision workers.
- Clarified stable distribution through release tags, submodules, plain clones, and release archives.
- Documented default wait settings: `--timeout 900` and `--interval 30`.
- Updated the README introduction from prototype wording to Baton CLI wording.

## v0.1.1

- Aligned CR reviewer permission behavior with the documented workflow.

## v0.1.0

- Added the SQLite-backed Baton CLI for handoff, CR review, shift control, stop/resume, and agent identity workflows.
