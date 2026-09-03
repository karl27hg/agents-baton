# Agent Bootstrap: Installed Baton

Use this guide when an agent must verify or initialize a project that uses a pipx-installed Baton command.

## Authority Rules

- Do not install, upgrade, pin, unpin, or uninstall Baton without explicit user or project-owner approval.
- Prefer an exact release tag or commit. Use a moving branch only when the user explicitly requests development validation.
- A pipx installation is shared by every project owned by the same operating-system user. Treat version changes and uninstall as cross-project operations.
- Do not edit Baton SQLite files directly.

## Delegation Policy

- While operating under Baton, do not create subagents, child tasks, parallel agent sessions, or delegated background agents.
- Do not use multi-agent or thread-creation tools to delegate Baton work.
- Delegate work only by registering Baton handoffs for configured project roles.
- A planner may register independent handoffs for parallel execution, but must not create or invoke the agents that execute them.
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

The apply command rechecks the source. It refuses a stale token, active waiters, in-progress handoffs, a layout move without global stop, an incompatible database, or a distinct existing target. It creates a SQLite backup before changing schema or moving layout and installs the project marker. For a layout move, it atomically redirects the legacy path to the canonical database with a relative symlink; the original content is retained in `.baton/backups/`.

After migration:

```bash
baton migrate --check
baton status
baton-report summary
```

Normal workflow commands do not migrate schemas automatically. A `database migration required` error is an operator action: keep workers stopped, run `baton migrate`, verify with `baton migrate --check`, and then resume the intended scopes.

## Role Guides

Read the appropriate installed guide before starting role work:

```bash
baton guide show worker
baton guide show planner
```

- `worker`: wait, claim, finish, CR review, shift, and reporting behavior.
- `planner`: parallel-safety, dependency, and Gate planning behavior.

Before a worker's first wait, require it to inspect `shift status --role <role>`. A worker may create the default `4h` role shift only when no applicable deadline or stopped/expired scope exists. Existing active deadlines are preserved, and expired or stopped role/global scopes require explicit user or SM authorization before restart, extension, or resume.

Project `AGENTS.md` should require these guides and define the assigned role. If a command, version, path, migration plan, or authority decision is unclear, stop and ask the user or SM instead of guessing.
