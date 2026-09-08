# Agent Bootstrap: Installed Baton

Use this guide when an agent must verify or initialize a project that uses a pipx-installed Baton command.

## Authority Rules

- Do not install, upgrade, pin, unpin, or uninstall Baton without explicit user or project-owner approval.
- Prefer an exact release tag or commit. Use a moving branch only when the user explicitly requests development validation.
- A pipx installation is shared by every project owned by the same operating-system user. Treat version changes and uninstall as cross-project operations.
- Do not edit Baton SQLite files directly.

## Delegation Policy

- While operating under Baton, do not create subagents, child tasks, parallel agent sessions, or delegated background agents.
- Do not use thread-creation tools to delegate Baton work.
- Delegate work only by registering Baton handoffs for configured project roles.
- A planner may register independent handoffs for parallel execution, but must not create or invoke the agents that execute them.
- When the project explicitly enables opt-in Codex peer notification, an agent may send a follow-up to an existing task recorded by `agent session-set`. The message must name a ready Baton handoff, and the receiver must inspect and claim it; notification is not permission or delegation state.
- Record each attempted host message with `notify record`. A failed or unavailable endpoint falls back to `wait`/`watch`.
- If no eligible role is available, wait or report the blocker to the SM or user. Do not bypass Baton by creating a subagent.

Baton cannot disable tools provided by the agent host. The project `AGENTS.md` or equivalent host policy must repeat this rule when technical enforcement is required.

## Command And Project Checks

Run these checks before operating workflow state:

```bash
command -v baton
baton --version
pwd
```

Use command help for syntax and options. Use guides for role behavior and operational policy:

```bash
baton help
baton help project migrate
baton guide show worker
```

The default database is `<project-root>/.baton/baton.sqlite3`, selected by the nearest ancestor containing `.baton/project.json`. Git is not consulted. For an existing project, verify the resolved marker, DB, schema, and recorded migration version:

```bash
baton project info
```

For isolated Git worktrees, do not initialize each checkout. Set `BATON_DB` to the one
branch-independent control database, set `BATON_WORKSPACE_ROOT` to the current checkout,
and confirm that every project agent reports the same database path:

```bash
export BATON_DB=/absolute/path/to/project-control/.baton/baton.sqlite3
export BATON_WORKSPACE_ROOT="$PWD"
baton project info
```

If `project info` reports a configured VCS provider, read the project `baton.toml` and the version-matched Git guide, then inspect the workspace before claiming work:

```bash
baton guide show git
baton workspace check
```

For a new project, confirm the intended directory before initialization. `baton init` makes the current directory the root; use `baton init --project-root PATH` from elsewhere.

If a marker exists but its database is missing, do not initialize a replacement. Baton rejects this state so workflow history can be restored from backup.

If no database exists and the project is new, initialize it only with user or SM approval:

```bash
baton init
baton migrate --check
baton project info
```

If an existing `.baton/baton.sqlite3` has no marker, Baton can discover it from descendants. `baton init` adopts a current in-place DB by adding the marker, while `baton migrate` adds it after a required schema upgrade. If the database is elsewhere or in a legacy tool layout, inspect a migration plan instead of creating an empty DB.

## Existing Database Migration

Check the standard project path and supported legacy tool layouts without writing:

```bash
baton project migrate --check
```

If discovery fails, provide the known existing database path:

```bash
baton project migrate --check --source-db /path/to/existing/baton.sqlite3
```

The check rehearses migration on an in-memory copy and prints a `plan_token`. Review the source, target, schema versions, pending migrations, layout move, active waiter count, in-progress handoff count, and global stop state. Do not apply when the source or target is unexpected.

Finish or cancel in-progress handoffs, stop Baton workers, and set the project-local global maintenance stop. Then rerun the check because the stop changes the plan token:

```bash
baton stop --all --reason "Baton migration"
baton project migrate --check
```

Repeat the same path options and pass the exact token from that final check:

```bash
baton project migrate --apply --plan-token <token>
```

For an explicit source path:

```bash
baton project migrate --apply \
  --source-db /path/to/existing/baton.sqlite3 \
  --plan-token <token>
```

The apply command rechecks the source. It refuses a stale token, active waiters, in-progress or cancel-requested handoffs, a layout move without global stop, an incompatible database, or a distinct existing target. It creates a SQLite backup before changing schema or moving layout and installs the project marker. For a layout move, it atomically redirects the legacy path to the canonical database with a relative symlink; the original content is retained in `.baton/backups/`.

After migration:

```bash
baton migrate --check
baton status
baton-report summary
```

Normal workflow commands do not migrate schemas automatically. A `database migration required` error is an operator action: keep workers stopped, run `baton migrate`, verify with `baton migrate --check`, and then resume the intended scopes.

### Schema v12 Upgrade Notice

Installing a shared pipx executable does not modify any project database. Baton `v0.6.0rc3` and later use schema v12. Each existing project owns its own database and must be migrated separately from that project's root. Projects already on schema v12 need only `baton migrate --check`.

Before replacing the executable, run the read-only preflight with the currently compatible Baton. It returns exit `0` only when the project has a global maintenance stop and no active waiter, handoff, cancellation acknowledgement, or claimed CR review:

```bash
baton upgrade preflight
baton stop --all --reason "Baton upgrade"
baton upgrade preflight
```

Drain every listed object and repeat preflight until it reports `READY`. Only then replace the executable. For an existing schema v11 database, continue with:

```bash
baton migrate
baton migrate --check
baton resume --all
```

`baton migrate` writes a validated backup under `.baton/backups/` before applying schema v12 transactionally. Schema v12 preserves existing handoffs and notification records as attempt 1, adds retry-attempt-scoped notification deduplication, and adds audited replacement relationships for cancelled CR implementation handoffs. It does not infer replacement relationships from historical cancellations; a reviewer must record a valid replacement explicitly with `cr supersede-handoff` before such a CR can be marked implemented.

If the executable was replaced too early, the new Baton can still run `upgrade preflight` against a known older schema and print blocker IDs, but workflow transitions must be drained with the previous compatible Baton. Do not resume with an older Baton executable after schema v12 is applied. When multiple projects use the same pipx installation, repeat preflight and the project-local database migration for each project; there is no global database migration command.

Schema v7 introduces `failed` handoffs and `handoff.register`. Existing projects retain prior registration behavior because migration grants `handoff.register` to every active role already present. Review those grants after migration and use `role permission-remove <role> handoff.register` when registration should remain centralized in `sm` or `planning`.

Schema v8 adds submitted and approved CR body hashes. It does not guess an approval hash
for an existing approved CR. The assigned reviewer must inspect it with `cr show` and run
`cr seal` before creating or claiming new implementation work from that legacy CR.

## Role Guides

Read the appropriate installed guide before starting role work:

```bash
baton guide show worker
baton guide show planner
```

- `worker`: wait, claim, finish/fail, CR review, shift, and reporting behavior.
- `planner`: parallel safety, reconciliation handoffs, combined `watch`, Gate planning, failure decisions, and approved-design replacement.

Before a worker's first wait, require it to inspect `shift status --role <role>`. A worker may create the default `4h` role shift only when no applicable deadline or stopped/expired scope exists. Existing active deadlines are preserved, and expired or stopped role/global scopes require explicit user or SM authorization before restart, extension, or resume.

Require planner/SM roles that receive both CR reviews and handoffs to use `watch` rather than alternating long independent waits unless push-first conditions are satisfied. A reachable Codex planner may end its current turn without a CLI waiter when it owns no active handoff or review, every expected return path is an explicit planning handoff, and every producer can notify its active session. This is addressable idle, not a Baton workflow state. CR monitoring, unassigned role work, non-Codex hosts, stale endpoints, and notification failures retain the polling fallback. Require every active claimant to inspect its handoff before commit, integration, and completion. On `cancel_requested`, the claimant pauses and inspects the reason; it resumes only after an authorized `cancel-withdraw` restores `in_progress`, or uses `cancel-ack` after cancellation is confirmed. It must not report `finish` or `fail` while cancellation is requested.

Project `AGENTS.md` should require these guides and define the assigned role. If a command, version, path, migration plan, or authority decision is unclear, stop and ask the user or SM instead of guessing.
