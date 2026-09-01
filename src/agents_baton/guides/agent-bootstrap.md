# Agent Bootstrap: Installed Baton

Use this guide when an agent must verify or initialize a project that uses a pipx-installed Baton command.

## Authority Rules

- Do not install, upgrade, pin, unpin, or uninstall Baton without explicit user or project-owner approval.
- Prefer an exact release tag or commit. Use a moving branch only when the user explicitly requests development validation.
- A pipx installation is shared by every project owned by the same operating-system user. Treat version changes and uninstall as cross-project operations.
- Do not edit Baton SQLite files directly.

## Command And Project Checks

Run these checks before operating workflow state:

```bash
command -v baton
baton --version
git rev-parse --show-toplevel
```

Change to the reported project root before running Baton. The default database is `<project-root>/.baton/baton.sqlite3`.

If no database exists and the project is new, initialize it only with user or SM approval:

```bash
baton init
baton migrate --check
```

If the project used Baton previously, do not run `baton init` merely because the expected database was not found. Inspect a migration plan first.

## Existing Database Migration

Check the standard project path and supported legacy tool layouts without writing:

```bash
baton project migrate --check
```

If discovery fails, provide the known existing database path:

```bash
baton project migrate --check --source-db /path/to/existing/baton.sqlite3
```

The check rehearses migration on an in-memory copy and prints a `plan_token`. Review the source, target, schema versions, pending migrations, layout move, and active waiter count. Do not apply when the source or target is unexpected.

Stop Baton workers before applying. Repeat the same path options and pass the exact token:

```bash
baton project migrate --apply --plan-token <token>
```

For an explicit source path:

```bash
baton project migrate --apply \
  --source-db /path/to/existing/baton.sqlite3 \
  --plan-token <token>
```

The apply command rechecks the source. It refuses a stale token, active waiters, an incompatible database, or a distinct existing target. It creates a SQLite backup before changing schema or moving layout and retains a legacy source database.

After migration:

```bash
baton migrate --check
baton status
baton-report summary
```

## Role Guides

Read the appropriate installed guide before starting role work:

```bash
baton guide show worker
baton guide show planner
```

- `worker`: wait, claim, finish, CR review, shift, and reporting behavior.
- `planner`: parallel-safety, dependency, and Gate planning behavior.

Project `AGENTS.md` should require these guides and define the assigned role. If a command, version, path, migration plan, or authority decision is unclear, stop and ask the user or SM instead of guessing.
