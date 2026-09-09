# SQLite Schema

English (primary) | [한국어](schema.ko.md)

This document explains the SQLite tables used by the Baton.

The SQLite database is the runtime authority for handoff state in this Baton workflow. Agents should use `baton` commands instead of editing records directly.

## Overview

Tables:

- `schema_migrations`: ordered database migration history
- `database_metadata`: diagnostic Baton package versions recorded at creation and migration
- `roles`: canonical role definitions
- `role_aliases`: alternate role names that resolve to canonical roles
- `role_permissions`: workflow permissions granted to roles
- `handoff_jobs`: primary handoff records
- `handoff_evidence_corrections`: append-only corrections to completed commit evidence
- `handoff_dependencies`: dependency edges between handoff jobs
- `workflow_gates`: stable named barriers for future or manually resolved workflow stages
- `gate_owners`: roles authorized to resolve or transfer each Gate
- `handoff_gate_dependencies`: Gate requirements attached to handoff jobs
- `gate_events`: Gate ownership and lifecycle audit log
- `handoff_events`: audit log of state changes and operational events
- `handoff_failure_reviews`: failed handoff links to decision CRs and their resolution
- `handoff_controls`: stop/resume controls for wait loops
- `waiter_leases`: short-lived handoff and CR waiter heartbeats for automatic polling intervals
- `agent_sessions`: opt-in runtime host thread and model metadata for stable agent profiles
- `agent_workstreams`: specialized routing eligibility within a role
- `handoff_notifications`: audited peer-thread message delivery results
- `workspace_events`: optional Git commit provenance and policy outcomes for handoff transitions
- `change_requests`: CR workflow state and Markdown file pointer
- `cr_events`: audit log of CR state changes
- `cr_handoffs`: links CRs to revision or implementation handoffs
- `cr_handoff_supersessions`: audited replacement links for cancelled CR implementations

State-changing CLI commands use `BEGIN IMMEDIATE` transactions to serialize writes.

New timestamps use `YYYY-MM-DD HH:MM:SS.ffffff UTC`. Readers also accept legacy second-precision values, and audit reports use source-local event IDs to produce a deterministic order when old events share one timestamp.

## `schema_migrations`

Purpose:

- Records every database migration exactly once.
- Allows existing unversioned Baton databases to adopt the current schema without deleting workflow rows.
- Prevents an older Baton binary from modifying a database created by a newer schema version.

Columns:

| Column | Type | Required | Purpose |
| --- | --- | --- | --- |
| `version` | `integer primary key` | yes | Monotonically increasing migration version. |
| `name` | `text` | yes | Stable migration name. |
| `applied_at` | `text` | yes | UTC timestamp when the migration committed. |

`baton migrate` creates a validated SQLite backup before running pending migrations, applicable seed updates, `PRAGMA quick_check`, and `PRAGMA foreign_key_check` in one transaction. Any failure rolls back schema changes, seed changes, and migration records together. Normal workflow commands reject pending migrations, and migration is refused while waiters, active handoffs, cancellation acknowledgements, or claimed submitted CR reviews remain active. Full default permissions are seeded only for a new or unversioned database; later migrations add only permissions introduced by that migration, preserving project-specific revocations.

Known migrations:

```text
1 initial_schema
2 handoff_cancel_permission
3 named_gates
4 waiter_leases
5 database_metadata
6 workspace_provenance
7 handoff_failures
8 cr_body_integrity
9 plan_revision_controls
10 opt_in_thread_notifications
11 workstream_routing
12 retry_and_replacement_tracking
13 completion_evidence
```

`baton upgrade preflight` is a read-only operational check that can inspect a recognized older schema before the executable is replaced. It requires an explicit global stop and reports blocker object IDs. `baton migrate --check` then verifies that the database is at the latest known schema version after migration.

## `database_metadata`

Purpose:

- Records the Baton package version that created a new versioned DB when known.
- Records the package version and UTC time of the latest schema migration.
- Supports diagnosis through `baton project info`; it does not decide compatibility.

Columns:

| Column | Type | Required | Purpose |
| --- | --- | --- | --- |
| `key` | `text primary key` | yes | Stable metadata key. |
| `value` | `text` | yes | Diagnostic value. |
| `updated_at` | `text` | yes | UTC timestamp of the metadata update. |

Known keys are `created_with_baton_version`, `last_migrated_with_baton_version`, and `last_migrated_at`. A database first observed from an older unversioned layout records `unknown` for its creation version. Package-only upgrades do not rewrite this table when no schema migration is applied.

Future schema changes must append a new migration and increment `LATEST_SCHEMA_VERSION`. Never change an already-released migration in place.

## `workspace_events`

Purpose:

- Records optional Git provenance for handoff register, claim, and finish transitions.
- Connects workflow events to immutable commit IDs without copying Git history, diffs, or source contents.
- Records `warn` outcomes and authorized `strict` overrides for audit and reporting.

Columns:

| Column | Type | Required | Purpose |
| --- | --- | --- | --- |
| `id` | `integer primary key` | yes | Monotonic workspace event ID. |
| `entity_type` | `text` | yes | `project`, `handoff`, or reserved `cr` provenance scope. |
| `entity_id` | `text` | no | Handoff or CR identifier when applicable. |
| `operation` | `text` | yes | State transition such as `registered`, `claimed`, or `finished`. |
| `policy` | `text` | yes | Effective `warn` or `strict` policy. `off` creates no event. |
| `outcome` | `text` | yes | `accepted`, `warning`, or authorized `override`. |
| `head_commit` | `text` | no | HEAD commit observed at the transition. |
| `baseline_commit` | `text` | no | Earlier Baton commit used for ancestry comparison. |
| `branch` | `text` | no | Informational branch name or `DETACHED`. |
| `dirty` | `integer` | yes | `1` when Git reported working-tree changes. |
| `actor_role` | `text` | no | Role performing or authorizing the transition. |
| `message` | `text` | no | Warning details or override reason. |
| `created_at` | `text` | yes | UTC event timestamp. |

Migration 6 also grants `workspace.override` to the default `sm` role. Existing project-specific permissions remain unchanged except for this newly introduced permission.

## `roles`

Purpose:

- Defines canonical roles that can own handoff jobs.
- Prevents handoffs from targeting unknown roles.
- Allows role configuration to evolve without changing CLI code.

Columns:

| Column | Type | Required | Purpose |
| --- | --- | --- | --- |
| `role_id` | `text primary key` | yes | Canonical role key, for example `frontend` or `qa`. |
| `display_name` | `text` | yes | Human-readable role name. |
| `description` | `text` | no | Optional role description. |
| `active` | `integer` | yes | `1` means new work can target this role. |
| `created_at` | `text` | yes | UTC creation timestamp. |
| `updated_at` | `text` | yes | UTC update timestamp. |

Default seed roles:

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

## `role_aliases`

Purpose:

- Maps shorthand or legacy role names to canonical roles.
- Lets agents use aliases such as `fe` while records store `frontend`.

Columns:

| Column | Type | Required | Purpose |
| --- | --- | --- | --- |
| `alias` | `text primary key` | yes | Alias entered by a user or agent. |
| `role_id` | `text` | yes | Canonical role in `roles.role_id`. |

Example:

```text
alias=fe, role_id=frontend
```

## `role_permissions`

Purpose:

- Stores workflow action permissions separately from role identity.
- Allows reviewer roles to be configured without changing handoff ownership rules.
- Keeps review and administrative authority distinct from the ability to claim implementation handoffs.

Columns:

| Column | Type | Required | Purpose |
| --- | --- | --- | --- |
| `role_id` | `text` | yes | Canonical role in `roles.role_id`. |
| `permission` | `text` | yes | Permission key, for example `cr.review` or `cr.approve`. |

Primary key:

```text
(role_id, permission)
```

Seed permissions:

- `sm` receives all CR permissions, `handoff.cancel`, `handoff.register`, `handoff.evidence_correct`, `gate.manage`, and `workspace.override` on `init` or the migration that introduces each permission.
- `planning` receives the CR review permissions needed for failure and blocking-outcome decisions plus `handoff.cancel`, `handoff.register`, and `handoff.evidence_correct` in a new project.
- Migration 7 grants `handoff.register` to every active role already present in an upgraded project, preserving the registration access that was implicit before the permission existed. An SM may revoke those compatibility grants after reviewing project policy.

Known permissions:

```text
cr.admin
cr.review
cr.request_revision
cr.approve
cr.reject
cr.assign_implementation
cr.mark_implemented
handoff.cancel
handoff.register
handoff.evidence_correct
gate.manage
workspace.override
```

Use `role permission-add` and `role permission-remove` to manage grants. Removing a permission is an explicit project policy decision and repeated migrations do not restore the full default permission set.

## `handoff_jobs`

Purpose:

- Stores the main handoff queue record.
- Replaces file-location state such as `jobs/`, `blocked/`, and `finished/`.
- Provides the data used by `register`, `next`, `claim`, `finish`, and `status`.
- Records an explicit unsuccessful outcome through `fail` without releasing dependent work.

Columns:

| Column | Type | Required | Purpose |
| --- | --- | --- | --- |
| `job_id` | `text primary key` | yes | Stable handoff ID, for example `HO-2026-06-02-001`. |
| `title` | `text` | yes | Short human-readable title. |
| `status` | `text` | yes | Current queue state. |
| `target_role` | `text` | yes | Canonical role that may claim and finish the job. |
| `workstream` | `text` | no | Optional specialization that the claimant must register within `target_role`. |
| `attempt` | `integer` | yes | Current execution generation, starting at 1 and incremented by each reviewed retry. |
| `source_ref` | `text` | no | Source CR, QA report, user request, or document reference. |
| `objective` | `text` | yes | What the target role must accomplish. |
| `exit_criteria` | `text` | yes | Completion criteria for the target role. |
| `created_at` | `text` | yes | UTC creation timestamp. |
| `claimed_by` | `text` | no | Stable profile name or explicit claimant used at claim time. |
| `started_at` | `text` | no | UTC timestamp when claimed. |
| `finished_at` | `text` | no | UTC timestamp when finished. |
| `closure_evidence` | `text` | no | Required evidence when the job is finished. |
| `related_commit` | `text` | no | Original commit evidence recorded at completion. New explicit references resolve to canonical full commit IDs. |
| `related_commit_resolution` | `text` | yes | `not_provided`, `resolved`, `unresolved`, or migrated `legacy_unchecked`. |
| `related_commit_resolution_reason` | `text` | no | Required audited reason for an explicitly unresolved reference. |
| `completion_outcome` | `text` | yes | `unspecified`, `pass`, `fail`, `conditional`, or `inconclusive`. |
| `completion_blocking` | `integer` | yes | `1` when a non-pass completed result requires planning or review before success-dependent work. |
| `outcome_cr_id` | `text` | no | Optional existing CR that records the completion-result decision. |

Allowed `status` values:

```text
blocked
open
in_progress
cancel_requested
failed
finished
cancelled
```

Status meaning:

- `blocked`: Waiting for required upstream jobs to finish.
- `open`: Ready to be claimed by `target_role`.
- `in_progress`: Claimed by an agent profile.
- `cancel_requested`: An authorized role requested cancellation; the original claimant must pause while the request is reviewed, then either resume after withdrawal or acknowledge a confirmed cancellation.
- `failed`: The claimed work did not meet its exit criteria. Its failure CR awaits or records a planning decision.
- `finished`: Completed with closure evidence.
- `cancelled`: Intentionally stopped as a job, not merely paused.

An authorized `cancel` operation immediately changes a selected `blocked`, `open`, or reviewed `failed` job to `cancelled`, then recursively cancels blocked dependency descendants. An `in_progress` job changes to `cancel_requested`. Before claimant acknowledgement, `cancel-withdraw` by a role with `handoff.cancel` returns it to `in_progress`, preserving `claimed_by` and `started_at` and recording the review reason. Withdrawal is rejected when a linked implementation CR is already `cancelled` or `superseded`. Only `cancel-ack` by the claimant finalizes cancellation and descendant propagation. `cancel --force` is the audited recovery path when acknowledgement is impossible. A failed job must have its failure CR rejected or cancelled first. Unrelated queue branches are unchanged.

`fail` changes only an `in_progress` job to `failed`, creates and submits a linked failure CR, and leaves dependency descendants `blocked`. An approved failure CR allows its reviewer to use `retry`, which increments `attempt` and returns the original job to `open`. A rejected failure CR allows an authorized cancellation. Dependents become ready only after the retried original job reaches `finished`.

`finish` and `completion_outcome` describe different dimensions. `finished` means the assigned work completed and produced evidence; a completed validation may legitimately record `completion_outcome=fail`. Use lifecycle `failed` when the handoff itself could not meet its exit criteria and requires the failure-CR retry/cancel decision. `completion_blocking=1` is allowed only for `fail`, `conditional`, or `inconclusive`.

RC7 keeps `completion_blocking` immutable and derives `open_cr`, `implemented_cr`, `terminal_unimplemented_cr`, or `no_outcome_cr` from the current `outcome_cr_id` relationship. The legacy `outcome.blocking` and report `blocking_outcomes` values remain historical totals. A rejected, cancelled, or superseded CR is terminal-unimplemented, not an inferred resolution. No schema migration or backfill records a decision that was not explicitly audited.

An explicit completion commit is resolved as a local Git commit and stored canonically. An audited unresolved reference requires an explicit override and reason. Schema v13 preserves existing completion rows as `completion_outcome=unspecified` and marks existing commit references `legacy_unchecked` rather than claiming they were validated.

Minimal ready job example:

```text
job_id=HO-2026-06-02-001
title=Frontend upload follow-up
status=open
target_role=frontend
source_ref=cr:CR-2026-06-02-example
objective=Implement the approved upload follow-up.
exit_criteria=The approved behavior is implemented and verified.
created_at=2026-06-02 09:00:00 UTC
```

## `handoff_evidence_corrections`

Purpose:

- Appends a corrected commit reference without rewriting the original completion row.
- Preserves the previous effective reference, actor, reason, resolution result, and order of corrections.

Columns:

| Column | Type | Required | Purpose |
| --- | --- | --- | --- |
| `id` | `integer primary key` | yes | Monotonic correction ID. |
| `job_id` | `text` | yes | Finished handoff being corrected. |
| `previous_commit` | `text` | no | Effective commit before this correction. |
| `corrected_commit` | `text` | no | New canonical or explicitly unresolved reference. |
| `commit_resolution` | `text` | yes | `resolved` or `unresolved`. |
| `reason` | `text` | yes | Audited correction reason. |
| `actor_role` | `text` | yes | Role making the correction. |
| `actor_id` | `text` | yes | Concrete agent identity making the correction. |
| `created_at` | `text` | yes | UTC correction time. |

Only the original claimant acting under the target role, or a role with `handoff.evidence_correct`, may append a correction. `handoff show` derives the effective commit from the newest correction while retaining the original fields and full correction history.

## `handoff_dependencies`

Purpose:

- Stores dependency edges between jobs.
- Allows `finish`, Gate release, and reconciliation commands to determine when a `blocked` job can become `open`.
- Supports reverse inspection through `handoff successors` without assigning the downstream work.

Columns:

| Column | Type | Required | Purpose |
| --- | --- | --- | --- |
| `job_id` | `text` | yes | Dependent job waiting for another job. |
| `depends_on_job_id` | `text` | yes | Required upstream job. |

Primary key:

```text
(job_id, depends_on_job_id)
```

Example:

```text
job_id=HO-2026-06-02-003
depends_on_job_id=HO-2026-06-02-001
```

Promotion rule:

- A `blocked` job is promoted only when every `depends_on_job_id` is `finished`.
- This is an after-completion edge, not an after-success edge. A predecessor with `status=finished` and `completion_outcome=fail` satisfies it.
- Success-dependent work must also depend on a named Gate that a planner or reviewer releases only after accepting the structured outcome.
- `finish` promotes eligible direct successors in the same transaction as the upstream completion.
- A `failed` upstream is not successful completion. Its dependents remain `blocked` while its failure CR is reviewed and while a retry is pending.
- If any required upstream job is `cancelled`, Baton recursively changes its blocked dependents to `cancelled`.
- Each propagated transition records one `dependency_cancelled` handoff event with the immediate upstream job as its cause.
- A new handoff registered with an already-cancelled dependency starts as `cancelled`, not `blocked`.
- A new handoff whose declared dependencies are all already `finished` starts as `open` without a separate promotion command.
- Duplicate dependency IDs are rejected before the job is inserted.
- A new handoff registered against a `failed` dependency remains `blocked` and emits a warning. Standalone remediation should use `source_ref` for causality instead of depending on the failed job.
- `promote-ready` also reconciles older database records that still contain a blocked job behind a cancelled dependency.
- Independent jobs and dependency branches are never cancelled by this propagation.

## `handoff_failure_reviews`

Purpose:

- Links each failed handoff attempt to the automatically submitted decision CR.
- Keeps retry and cancellation decisions auditable without treating failure as successful completion.
- Supports multiple failure/retry attempts for the same handoff.

Each row records `job_id`, unique `cr_id`, failure role, reason, optional evidence, failure time, and an optional `retry` or `cancelled` resolution with its deciding role, time, and message. An unresolved row is the active failure review for that job. The CR reviewer must have `handoff.register` and the required CR review permissions. The default reviewer is `planning`, except a failure by `planning` defaults to `sm` to prevent self-review.

| Column | Type | Required | Purpose |
| --- | --- | --- | --- |
| `id` | `integer primary key` | yes | Monotonic failure-attempt identifier. |
| `job_id` | `text` | yes | Failed handoff. |
| `cr_id` | `text unique` | yes | Automatically submitted failure CR. |
| `failed_by_role` | `text` | yes | Target role reporting failure. |
| `reason` | `text` | yes | Concrete failure reason. |
| `evidence` | `text` | no | Test output or other supporting evidence. |
| `failed_at` | `text` | yes | UTC failure time. |
| `resolution` | `text` | no | `retry`, `cancelled`, or null while under review. |
| `resolved_by_role` | `text` | no | Role applying the reviewed decision. |
| `resolved_at` | `text` | no | UTC resolution time. |
| `resolution_message` | `text` | no | Required retry or cancellation reason. |

## Named Gate Tables

`workflow_gates` stores stable names that can exist before a concrete predecessor handoff is created.

| Column | Type | Required | Purpose |
| --- | --- | --- | --- |
| `gate_name` | `text primary key` | yes | Normalized stable Gate name. |
| `status` | `text` | yes | `pending`, `released`, or `cancelled`. |
| `created_by_role` | `text` | yes | Role that created the Gate. |
| `created_at` | `text` | yes | UTC creation timestamp. |
| `resolved_at` | `text` | no | UTC release or cancellation timestamp. |
| `resolution_evidence` | `text` | no | Required release evidence or cancellation reason. |

`gate_owners` uses `(gate_name, role_id)` as its primary key. The creator role is the default owner when `gate create` has no `--owner-role`; repeated `--owner-role` values create joint ownership.

`handoff_gate_dependencies` uses `(job_id, gate_name)` as its primary key. A handoff remains `blocked` until all handoff dependencies are `finished` and all Gate dependencies are `released`. A handoff registered behind an already-cancelled Gate starts as `cancelled`.

`gate_events` records `created`, `released`, `cancelled`, and `ownership_transferred` events with actor role, status transition, reason or evidence, and UTC timestamp.

Gate authority rules:

- An owner may release, cancel, or transfer a pending Gate.
- A role with `gate.manage` may transfer ownership for emergency recovery, but cannot directly release or cancel a Gate it does not own.
- `gate transfer` replaces the complete owner set and requires an audit reason.
- Releasing a Gate promotes eligible handoffs in the same transaction.
- Cancelling a Gate cancels only blocked direct dependents and their blocked handoff descendants; unrelated queue branches remain unchanged.
- Baton records role authority but does not authenticate an individual human user.

## `handoff_events`

Purpose:

- Provides an audit log for workflow operations.
- Records who changed a job, when it changed, and why.
- Lets agents and humans verify claim identity and lifecycle history.

Columns:

| Column | Type | Required | Purpose |
| --- | --- | --- | --- |
| `id` | `integer primary key autoincrement` | yes | Event sequence. |
| `job_id` | `text` | no | Related job ID, if any. |
| `event_type` | `text` | yes | Event name, for example `registered`, `claimed`, `finished`. |
| `actor_role` | `text` | no | Role that performed the operation. |
| `actor_id` | `text` | no | Stable profile name or explicit agent identity. |
| `from_status` | `text` | no | Previous status. |
| `to_status` | `text` | no | New status. |
| `message` | `text` | no | Evidence, reason, or event detail. |
| `created_at` | `text` | yes | UTC event timestamp. |

Current event types:

```text
role_added
role_alias_added
role_permission_added
role_permission_removed
agent_session_active
agent_session_replaced
agent_session_inactive
registered
claimed
finished
promoted
cancelled
cancellation_requested
cancellation_withdrawn
cancellation_acknowledged
cancellation_forced
dependency_cancelled
gate_cancelled
control_stopped
control_resumed
shift_started
shift_extended
shift_ended
notification_sent
notification_failed
```

Claim event example:

```text
event_type=claimed
job_id=HO-2026-06-02-001
actor_role=frontend
actor_id=frontend-main
from_status=open
to_status=in_progress
created_at=2026-06-02 09:10:00 UTC
```

## `handoff_controls`

Purpose:

- Stores stop/resume controls for wait loops.
- Stores optional shift deadlines for role agent operating windows.
- Replaces file flag checks in the Baton SQLite workflow.
- Does not change job status.

Columns:

| Column | Type | Required | Purpose |
| --- | --- | --- | --- |
| `scope` | `text primary key` | yes | Control scope, for example `all` or `role:frontend`. |
| `stopped` | `integer` | yes | `1` stops matching wait loops, `0` allows them. |
| `reason` | `text` | no | Human-readable stop reason. |
| `work_until` | `text` | no | UTC shift deadline. When expired, Baton marks the scope stopped. |
| `updated_at` | `text` | yes | UTC timestamp of last control update. |

Scopes:

```text
all
role:frontend
role:qa
role:sm
```

Wait behavior:

1. Check `handoff_controls` for `all` or `role:<role>`.
2. If `work_until` is expired, mark the scope stopped.
3. Exit with code `3` if stopped.
4. Run promotion and queue check only if not stopped.

`cr wait-review` uses the same control scopes and exit codes.

Claim behavior:

- `claim` checks the same controls before starting new work.
- `finish` does not check shift controls, so already-claimed work can be reported after shift expiry. It rejects `cancel_requested`; review must either restore `in_progress` with `cancel-withdraw` or the claimant must use `cancel-ack` after cancellation is confirmed.

## `waiter_leases`

Purpose:

- Tracks active `wait`, `cr wait-review`, and combined `watch` processes sharing one Baton database.
- Provides the active waiter count used by the default automatic polling interval.
- Stores ephemeral coordination state, not workflow history or audit evidence.

Columns:

| Column | Type | Required | Purpose |
| --- | --- | --- | --- |
| `waiter_id` | `text primary key` | yes | Process-local UUID generated when a wait command starts. |
| `wait_kind` | `text` | yes | `handoff`, `cr_review`, or `watch`. |
| `role_id` | `text` | yes | Canonical role associated with the waiter. |
| `started_at` | `text` | yes | UTC timestamp when this wait command registered. |
| `heartbeat_at` | `text` | yes | UTC timestamp of its latest polling heartbeat. |
| `lease_expires_at` | `text` | yes | UTC heartbeat expiry. Automatic waits use 30 seconds; long fixed intervals include a 5-second grace. |

Behavior:

- Normal timeout, stop, ready-work, and error exits remove the lease in a `finally` cleanup.
- If a process or pipe disconnects before cleanup, a later waiter heartbeat deletes the expired lease.
- Automatic mode targets `min(30, 3 * active waiters)` seconds and adds a small stable jitter to avoid synchronized polling.
- A numeric `--interval` remains fixed for that process, but its lease is included in the count used by automatic waiters.
- A fixed interval over 25 seconds uses a lease of `interval + 5` seconds so a healthy sleeping process is not removed as stale.
- Lease rows are not included in `baton-report` audit or workflow summaries.

## `agent_sessions`

Purpose:

- Maps a stable Baton agent profile to one active runtime endpoint when peer notification is explicitly used.
- Records the host, thread ID, role, and model needed for an agent to select an existing Codex task.
- Keeps inactive endpoint history without treating runtime IDs as authorization or durable identity.

Columns:

| Column | Type | Required | Purpose |
| --- | --- | --- | --- |
| `session_id` | `text primary key` | yes | Baton-generated runtime-session UUID. |
| `agent_id` | `text` | yes | Stable profile identity also used by `claimed_by`. |
| `role_id` | `text` | yes | Canonical role advertised by the session. |
| `host` | `text` | yes | Messaging host, initially `codex`. |
| `thread_id` | `text` | yes | Host-specific existing task identifier. |
| `model` | `text` | yes | Exact model metadata supplied at registration. |
| `status` | `text` | yes | `active` or `inactive`. |
| `created_at` | `text` | yes | Initial registration time. |
| `updated_at` | `text` | yes | Latest registration or lifecycle update. |
| `ended_at` | `text` | no | Time an endpoint was deactivated. |
| `end_reason` | `text` | no | Audited reason for deactivation or replacement. |

`unique(host, thread_id)` prevents one host thread from representing two profiles, and a partial unique index permits only one active endpoint per `agent_id`. Replacing a session requires explicit `--replace`. `active` means addressable by a future follow-up, not currently executing. A planner may rely on that addressability instead of a waiter lease only under the documented push-first idle conditions; the session row itself does not prove complete notification coverage.

## `agent_workstreams`

Purpose:

- Separates role authorization from specialized routing eligibility.
- Lets agents in one broad role advertise stable domains such as `api-contract` or `ui-regression`.

Columns:

| Column | Type | Required | Purpose |
| --- | --- | --- | --- |
| `agent_id` | `text` | yes | Stable concrete agent profile. |
| `role_id` | `text` | yes | Role in which the specialization applies. |
| `workstream` | `text` | yes | Normalized domain route. |
| `created_at` | `text` | yes | UTC registration timestamp. |

The composite primary key is `(agent_id, role_id, workstream)`. A null workstream on a handoff or CR preserves role-only routing. Workstream registration grants no role permission.

## `handoff_notifications`

Purpose:

- Records the result after an agent attempts to notify an existing peer thread.
- Snapshots sender and recipient profile/model metadata for audit.
- Prevents repeated successful wake-up messages without changing handoff ownership.

Columns:

| Column | Type | Required | Purpose |
| --- | --- | --- | --- |
| `id` | `integer primary key autoincrement` | yes | Delivery-attempt sequence. |
| `job_id` | `text` | yes | Ready receiving handoff named in the message. |
| `attempt` | `integer` | yes | Handoff execution generation associated with this delivery record. |
| `sender_session_id` | `text` | yes | Sending runtime session. |
| `recipient_session_id` | `text` | yes | Selected existing peer runtime session. |
| `sender_agent_id` | `text` | yes | Stable sender profile snapshot. |
| `sender_model` | `text` | yes | Sender model snapshot. |
| `recipient_agent_id` | `text` | yes | Stable recipient profile snapshot. |
| `recipient_thread_id` | `text` | yes | Host task that received the attempt. |
| `recipient_model` | `text` | yes | Recipient model snapshot. |
| `transport` | `text` | yes | Runtime host used for delivery. |
| `delivery_status` | `text` | yes | Compatibility storage value: `sent` (host accepted) or `failed`. |
| `message_ref` | `text` | no | Optional host delivery/message reference. |
| `detail` | `text` | no | Result detail; required by CLI for failures. |
| `created_at` | `text` | yes | Attempt time. |

A partial unique index permits at most one `sent` row per `(job_id, attempt)`. CLI text presents that stored value as `host_accepted`; it is not recipient acknowledgement. `notify status` derives accepted-unclaimed, stale-unclaimed, and claimed outcomes from the current handoff and latest delivery record without adding a second authoritative state. Failed delivery records remain available for fallback diagnosis. A reviewed retry increments the handoff attempt, permitting one new successful delivery record for the corrected baseline while retaining earlier audit rows. `finish` normally promotes ready direct dependents; `notify targets` retains the same scoped promotion as a compatibility reconciliation path and returns active Codex peer candidates that match the optional workstream and do not currently own an `in_progress` or `cancel_requested` handoff or claimed submitted CR review. An applicable project-local global or target-role stop, including an expired shift, returns `outside_shift` without a candidate and leaves the handoff `open`. It does not send a message, and compatible peer messaging is not assumed for other model hosts. `notify record` records what the agent reports after using a host messaging tool. Neither operation claims the handoff. Authentication tokens and message bodies are not stored.

## `change_requests`

Purpose:

- Stores CR workflow state and metadata.
- Points to a Markdown file that contains the editable CR body.
- Treats SQLite as the authority for state while Markdown frontmatter is a Baton-managed projection.

Columns:

| Column | Type | Required | Purpose |
| --- | --- | --- | --- |
| `cr_id` | `text primary key` | yes | Stable CR ID, for example `CR-2026-06-02-001`. |
| `title` | `text` | yes | Short human-readable title. |
| `status` | `text` | yes | Current CR workflow state. |
| `author_role` | `text` | yes | Role responsible for the CR body. |
| `reviewer_role` | `text` | yes | Role allowed to review this CR. |
| `reviewer_workstream` | `text` | no | Optional specialization required of the concrete reviewer. |
| `review_claimed_by` | `text` | no | Concrete agent that currently owns or completed the review claim. |
| `review_started_at` | `text` | no | UTC timestamp of the latest review claim. |
| `file_path` | `text` | yes | Markdown body file path. |
| `created_at` | `text` | yes | UTC creation timestamp. |
| `updated_at` | `text` | yes | UTC update timestamp. |
| `submitted_at` | `text` | no | Last submission timestamp. |
| `approved_at` | `text` | no | Approval timestamp. |
| `rejected_at` | `text` | no | Final rejection timestamp. |
| `implemented_at` | `text` | no | Implementation completion timestamp. |
| `revision_count` | `integer` | yes | Number of revision requests. |
| `active_revision_job_id` | `text` | no | Open revision handoff, if any. |
| `submitted_body_hash` | `text` | no | SHA-256 of the Markdown body captured by the latest submit or resubmit. |
| `approved_body_hash` | `text` | no | SHA-256 of the immutable body approved by the reviewer. Null identifies a legacy unsealed approval. |
| `superseded_by_cr_id` | `text` | no | Approved replacement CR when this approval is retired by `cr supersede`. |
| `superseded_by_ref` | `text` | no | Immutable authoritative design reference used instead of a replacement CR. |

Allowed `status` values:

```text
draft
submitted
revision_requested
approved
rejected
implemented
superseded
cancelled
```

State rules:

- `draft -> submitted` is performed by the author role.
- `submitted -> revision_requested`, `approved`, or `rejected` is performed by the reviewer role.
- A workstream-routed submitted review must be claimed by an eligible concrete agent before a decision. Only that claimant may decide it.
- Resubmission and reviewer reassignment clear the previous review claim. `cr release-review` clears an undecided submitted claim with an audited reason.
- CLI and managed Markdown project current ownership as `active_review_claimed_by` only for submitted CRs and preserve historical attribution as `last_review_claimed_by`. The legacy frontmatter `review_claimed_by` alias mirrors the active value; SQLite and JSON retain it as the compatibility historical value.
- `revision_requested -> submitted` is performed by the author role after editing the Markdown body.
- Approval requires the current body to match `submitted_body_hash` and records `approved_body_hash`.
- Implementation handoff creation, claim, finish, and final implementation marking require the approved body hash to remain unchanged.
- Existing approved CRs migrated without a hash require an explicit reviewer `cr seal` before new implementation work.
- `approved -> implemented` requires at least one linked implementation handoff. Every linked implementation must be finished and non-blocking or be `cancelled` with an explicit replacement chain that reaches a finished, non-blocking implementation.
- `approved -> superseded` requires `cr.admin` and either an approved replacement CR or an immutable authoritative design reference from a role with `handoff.register`. Linked queued implementation handoffs are cancelled, linked active handoffs receive `cancel_requested`, and finished handoffs are preserved.
- `cancelled` is performed by a role with `cr.admin`, retires linked unfinished implementation work using the same cancellation rules, and records an audit event.
- `reviewer_role` can be reassigned before terminal review by a role with `cr.admin`.

## `cr_events`

Purpose:

- Provides an audit log for CR workflow operations.
- Records reviewer decisions, author resubmissions, and implementation handoff links.

Columns:

| Column | Type | Required | Purpose |
| --- | --- | --- | --- |
| `id` | `integer primary key autoincrement` | yes | Event sequence. |
| `cr_id` | `text` | yes | Related CR ID. |
| `event_type` | `text` | yes | Event name, for example `submitted` or `approved`. |
| `actor_role` | `text` | no | Role that performed the operation. |
| `from_status` | `text` | no | Previous CR status. |
| `to_status` | `text` | no | New CR status. |
| `message` | `text` | no | Evidence, reason, or linked job ID. |
| `created_at` | `text` | yes | UTC event timestamp. |

Current CR event types:

```text
created
submitted
resubmitted
revision_requested
approved
body_sealed
rejected
reviewer_reassigned
cancelled
superseded
supersedes
implementation_handoff_created
implementation_handoff_linked
implementation_handoff_superseded
implemented
```

## `cr_handoffs`

Purpose:

- Links CRs to generated or explicitly adopted handoff jobs.
- Distinguishes revision handoffs from implementation handoffs.
- Allows `cr mark-implemented` to enforce implementation completion.

Columns:

| Column | Type | Required | Purpose |
| --- | --- | --- | --- |
| `cr_id` | `text` | yes | Related CR ID. |
| `job_id` | `text` | yes | Related handoff job ID. |
| `kind` | `text` | yes | `revision` or `implementation`. |
| `created_at` | `text` | yes | UTC link creation timestamp. |

Primary key:

```text
(cr_id, job_id)
```

`cr link-handoff` adds an implementation link only after the assigned reviewer passes both `cr.review` and `cr.assign_implementation` authorization and Baton validates an exact `cr:<CR-ID>` source reference, finished non-blocking completion, and verifiable effective commit evidence. It emits `implementation_handoff_linked`, is idempotent for an existing identical implementation link, and rejects implementation reuse across CRs. Schema migration never infers or backfills these relationships.

Default CR inspection reports a mechanically eligible `implementation_adoption_candidate` only while the CR is approved and has no official implementation link. `--include-related-handoffs` replaces candidate output with neutral `related_handoff_unlinked` records for auditing. Source reference alone never establishes implementation purpose.

`cr mark-implemented` requires each implementation path, including any audited replacement chain, to end in a finished result with `completion_blocking=0`.

## `cr_handoff_supersessions`

Purpose:

- Records that a cancelled implementation handoff was explicitly replaced under the same approved CR.
- Preserves the retired job and its events while allowing CR closure only after the replacement chain finishes.
- Prevents migration or `mark-implemented` from guessing that an unrelated cancellation is complete.

Columns:

| Column | Type | Required | Purpose |
| --- | --- | --- | --- |
| `cr_id` | `text` | yes | Approved CR shared by the retired and replacement implementation handoffs. |
| `retired_job_id` | `text` | yes | Cancelled implementation handoff. |
| `replacement_job_id` | `text` | yes | Viable implementation handoff replacing the retired route. |
| `actor_role` | `text` | yes | Assigned reviewer role that recorded the replacement. |
| `reason` | `text` | yes | Audited replacement reason. |
| `created_at` | `text` | yes | UTC relationship creation time. |

The primary key is `(cr_id, retired_job_id)`. `cr supersede-handoff` requires both jobs to be linked as implementations of the same approved CR, requires the old job to be `cancelled`, and rejects a failed or cancelled replacement.

## Indexes

Indexes:

```sql
idx_handoff_jobs_status_role on handoff_jobs(status, target_role)
idx_handoff_dependencies_job on handoff_dependencies(job_id)
idx_handoff_dependencies_dep on handoff_dependencies(depends_on_job_id)
idx_handoff_events_job on handoff_events(job_id)
idx_handoff_evidence_corrections_job on handoff_evidence_corrections(job_id, id)
idx_handoff_gate_dependencies_job on handoff_gate_dependencies(job_id)
idx_handoff_gate_dependencies_gate on handoff_gate_dependencies(gate_name)
idx_gate_events_gate on gate_events(gate_name)
idx_agent_sessions_active_agent on agent_sessions(agent_id) where status = 'active'
idx_agent_sessions_role_status on agent_sessions(role_id, status, updated_at)
idx_handoff_notifications_job on handoff_notifications(job_id, id)
idx_handoff_notifications_sent_attempt on handoff_notifications(job_id, attempt) where delivery_status = 'sent'
idx_handoff_notifications_recipient on handoff_notifications(recipient_agent_id, created_at)
idx_cr_status_reviewer on change_requests(status, reviewer_role)
idx_cr_handoffs_cr on cr_handoffs(cr_id)
idx_cr_handoff_supersessions_replacement on cr_handoff_supersessions(cr_id, replacement_job_id)
```

Purpose:

- `status, target_role`: Fast `next --role` and status filtering.
- `dependencies.job_id`: Fast dependency lookup for a job.
- `dependencies.depends_on_job_id`: Fast reverse dependency analysis.
- `events.job_id`: Fast event history lookup.
- `handoff_evidence_corrections`: Fast effective-evidence lookup and ordered audit display.
- `handoff_gate_dependencies`: Fast Gate checks by job and dependent-job lookup by Gate.
- `gate_events.gate_name`: Fast Gate audit history lookup.
- `agent_sessions`: Unique active profile endpoints and fast role candidate lookup.
- `handoff_notifications`: Fast job/recipient audit lookup and one successful delivery per job attempt.
- `cr.status, reviewer_role`: Fast `cr wait-review` lookup.
- `cr_handoffs.cr_id`: Fast implementation completion checks.
- `cr_handoff_supersessions`: Fast replacement-chain validation for CR closure.

## Identity Model

The database records `claimed_by` on `handoff_jobs`, `actor_id` on `handoff_events`, and opt-in runtime/model metadata in `agent_sessions` and `handoff_notifications`.

Policy:

- Use a stable profile name as the long-lived identity.
- Examples: `frontend-main`, `qa-regression`, `sm`.
- Do not rely on Codex thread IDs, turn IDs, or temporary files as the only long-lived identity.
- Thread IDs and model names are notification metadata, not routing authority or permission inputs.

CLI identity resolution order:

1. `--claimed-by`
2. `BATON_AGENT_ID`
3. `--agent-id-file` or `BATON_AGENT_ID_FILE`
4. role name

## Lifecycle Example

Register:

```text
handoff_jobs.status=open
handoff_events.event_type=registered
```

Claim:

```text
handoff_jobs.status=in_progress
handoff_jobs.claimed_by=frontend-main
handoff_jobs.started_at=<utc>
handoff_events.event_type=claimed
```

Finish:

```text
handoff_jobs.status=finished
handoff_jobs.finished_at=<utc>
handoff_jobs.closure_evidence=<evidence>
handoff_jobs.completion_outcome=pass
handoff_jobs.completion_blocking=0
handoff_jobs.related_commit_resolution=resolved
handoff_events.event_type=finished
```

Blocked dependency flow:

```text
handoff_jobs.status=blocked
handoff_dependencies records dependency edges
finish updates eligible direct successors to open after dependencies are finished
promote-ready reconciles older or externally restored state
handoff_events.event_type=promoted
```

Named Gate flow:

```text
workflow_gates.status=pending
gate_owners records one or more resolving roles
handoff_gate_dependencies links blocked jobs to the Gate
gate release changes status to released and promotes eligible jobs transactionally
gate cancel changes status to cancelled and cancels only affected blocked branches
gate_events records every ownership and lifecycle decision
```
