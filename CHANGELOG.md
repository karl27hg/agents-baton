# Changelog

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
