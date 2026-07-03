# SQLite Schema

This document explains the SQLite tables used by the Baton.

The SQLite database is the runtime authority for handoff state in this Baton workflow. Agents should use `baton` commands instead of editing records directly.

## Overview

Tables:

- `roles`: canonical role definitions
- `role_aliases`: alternate role names that resolve to canonical roles
- `role_permissions`: workflow permissions granted to roles
- `handoff_jobs`: primary handoff records
- `handoff_dependencies`: dependency edges between handoff jobs
- `handoff_events`: audit log of state changes and operational events
- `handoff_controls`: stop/resume controls for wait loops
- `change_requests`: CR workflow state and Markdown file pointer
- `cr_events`: audit log of CR state changes
- `cr_handoffs`: links CRs to revision or implementation handoffs

State-changing CLI commands use `BEGIN IMMEDIATE` transactions to serialize writes.

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
- Keeps CR review authority distinct from the ability to claim implementation handoffs.

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

- `sm` receives all CR permissions on `init`.

Known CR permissions:

```text
cr.admin
cr.review
cr.request_revision
cr.approve
cr.reject
cr.assign_implementation
cr.mark_implemented
```

## `handoff_jobs`

Purpose:

- Stores the main handoff queue record.
- Replaces file-location state such as `jobs/`, `blocked/`, and `finished/`.
- Provides the data used by `register`, `next`, `claim`, `finish`, and `status`.

Columns:

| Column | Type | Required | Purpose |
| --- | --- | --- | --- |
| `job_id` | `text primary key` | yes | Stable handoff ID, for example `HO-2026-06-02-001`. |
| `title` | `text` | yes | Short human-readable title. |
| `status` | `text` | yes | Current queue state. |
| `target_role` | `text` | yes | Canonical role that may claim and finish the job. |
| `source_ref` | `text` | no | Source CR, QA report, user request, or document reference. |
| `objective` | `text` | yes | What the target role must accomplish. |
| `exit_criteria` | `text` | yes | Completion criteria for the target role. |
| `created_at` | `text` | yes | UTC creation timestamp. |
| `claimed_by` | `text` | no | Stable profile name or explicit claimant used at claim time. |
| `started_at` | `text` | no | UTC timestamp when claimed. |
| `finished_at` | `text` | no | UTC timestamp when finished. |
| `closure_evidence` | `text` | no | Required evidence when the job is finished. |
| `related_commit` | `text` | no | Commit SHA or reference for completed output. |

Allowed `status` values:

```text
blocked
open
in_progress
finished
cancelled
```

Status meaning:

- `blocked`: Not ready because dependencies or manual blockers remain.
- `open`: Ready to be claimed by `target_role`.
- `in_progress`: Claimed by an agent profile.
- `finished`: Completed with closure evidence.
- `cancelled`: Intentionally stopped as a job, not merely paused.

Minimal ready job example:

```text
job_id=HO-2026-06-02-001
title=Frontend upload follow-up
status=open
target_role=frontend
source_ref=docs/change-requests/CR-2026-06-02-example.md
objective=Implement the approved upload follow-up.
exit_criteria=The approved behavior is implemented and verified.
created_at=2026-06-02 09:00:00 UTC
```

## `handoff_dependencies`

Purpose:

- Stores dependency edges between jobs.
- Allows `promote-ready` to determine when a `blocked` job can become `open`.

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
registered
claimed
finished
promoted
control_stopped
control_resumed
shift_started
shift_extended
shift_ended
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
- `finish` does not check shift controls, so already-claimed work can be reported after shift expiry.

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
| `file_path` | `text` | yes | Markdown body file path. |
| `created_at` | `text` | yes | UTC creation timestamp. |
| `updated_at` | `text` | yes | UTC update timestamp. |
| `submitted_at` | `text` | no | Last submission timestamp. |
| `approved_at` | `text` | no | Approval timestamp. |
| `rejected_at` | `text` | no | Final rejection timestamp. |
| `implemented_at` | `text` | no | Implementation completion timestamp. |
| `revision_count` | `integer` | yes | Number of revision requests. |
| `active_revision_job_id` | `text` | no | Open revision handoff, if any. |

Allowed `status` values:

```text
draft
submitted
revision_requested
approved
rejected
implemented
cancelled
```

State rules:

- `draft -> submitted` is performed by the author role.
- `submitted -> revision_requested`, `approved`, or `rejected` is performed by the reviewer role.
- `revision_requested -> submitted` is performed by the author role after editing the Markdown body.
- `approved -> implemented` requires at least one linked implementation handoff and all linked implementation handoffs must be `finished`.
- `cancelled` is performed by a role with `cr.admin` and records an audit event.
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
rejected
reviewer_reassigned
cancelled
implementation_handoff_created
implemented
```

## `cr_handoffs`

Purpose:

- Links CRs to generated handoff jobs.
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

## Indexes

Indexes:

```sql
idx_handoff_jobs_status_role on handoff_jobs(status, target_role)
idx_handoff_dependencies_job on handoff_dependencies(job_id)
idx_handoff_dependencies_dep on handoff_dependencies(depends_on_job_id)
idx_handoff_events_job on handoff_events(job_id)
idx_cr_status_reviewer on change_requests(status, reviewer_role)
idx_cr_handoffs_cr on cr_handoffs(cr_id)
```

Purpose:

- `status, target_role`: Fast `next --role` and status filtering.
- `dependencies.job_id`: Fast dependency lookup for a job.
- `dependencies.depends_on_job_id`: Fast reverse dependency analysis.
- `events.job_id`: Fast event history lookup.
- `cr.status, reviewer_role`: Fast `cr wait-review` lookup.
- `cr_handoffs.cr_id`: Fast implementation completion checks.

## Identity Model

The database records `claimed_by` on `handoff_jobs` and `actor_id` on `handoff_events`.

Policy:

- Use a stable profile name as the long-lived identity.
- Examples: `frontend-main`, `qa-regression`, `sm`.
- Do not rely on Codex thread IDs, turn IDs, or temporary files as the only long-lived identity.

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
handoff_events.event_type=finished
```

Blocked dependency flow:

```text
handoff_jobs.status=blocked
handoff_dependencies records dependency edges
promote-ready updates status to open after dependencies are finished
handoff_events.event_type=promoted
```
