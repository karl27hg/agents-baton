# Using Baton In Other Projects

This guide explains how to add Baton to another repository as a local workflow tool.

## Recommended Layout

Use Baton as a project-local tool and keep runtime state inside the consuming project.

```text
your-project/
├── .baton/
├── docs/change-requests/
├── tools/baton/
└── AGENTS.md
```

- `.baton/`: local Baton runtime state, ignored by git.
- `docs/change-requests/`: CR Markdown files created or managed by Baton.
- `tools/baton/`: Baton repository, usually as a submodule.
- `AGENTS.md`: project-specific agent rules.

## Recommended Distribution Model

For normal project use, prefer pinning Baton to a release tag instead of tracking a moving branch.

Recommended options:

- Git submodule pinned to a release tag: best when the consuming project should record the exact Baton version.
- Git clone plus tag checkout: acceptable for local-only use, but the consuming project will not record the expected Baton revision unless you document it separately.
- Release archive download: useful for one-off installation, but harder to update consistently than a submodule.

Plain `git clone` is fine for experimentation. For stable use across projects, use a tag such as `v0.2.0` and update intentionally when a new Baton release is chosen.

## Add Baton As A Submodule

Submodules are the preferred option when the consuming project should pin a Baton version.

```bash
mkdir -p tools
git submodule add git@github.com:karl27hg/agents-baton.git tools/baton
```

Pin to a release tag:

```bash
cd tools/baton
git checkout v0.2.0
cd ../..
git add tools/baton
git commit -m "Add Baton workflow tool"
```

Run Baton from the consuming project root:

```bash
tools/baton/bin/baton init
tools/baton/bin/baton role list
tools/baton/bin/baton shift start --role frontend
tools/baton/bin/baton wait --role frontend
```

The last command uses Baton's default bounded wait settings:

```text
--timeout 900
--interval 3
```

## Add Baton As A Plain Clone

Use a plain clone when you do not want submodule management.

```bash
mkdir -p tools
git clone git@github.com:karl27hg/agents-baton.git tools/baton
```

This is simpler, but the consuming project will not automatically record which Baton revision it expects.

For stable use, check out a release tag after cloning:

```bash
cd tools/baton
git fetch --tags
git checkout v0.2.0
```

Record the selected version in the consuming project's documentation or onboarding notes.

## Use A Release Archive

GitHub Releases can also be used when a project should vendor Baton without submodule metadata.

Download the selected release archive, extract it under `tools/baton`, and record the version in the consuming project's documentation.

This is less convenient for updates than a submodule, but it keeps the consuming repository independent from Git submodule workflows.

## Runtime State

Baton defaults to this database path when run from the project root:

```text
.baton/baton.sqlite3
```

The default local agent identity file is:

```text
.baton/agent-id
```

Use `--db` only when you need a different database:

```bash
tools/baton/bin/baton --db /tmp/baton.sqlite3 init
```

## Git Ignore

Add Baton runtime files to the consuming project's `.gitignore`:

```gitignore
.baton/
*.sqlite3
*.sqlite3-shm
*.sqlite3-wal
```

Do not ignore CR Markdown files if they are part of the project workflow. They should usually be reviewed and committed like other project documents.

## AGENTS.md

Add project-specific Baton rules to the consuming project's `AGENTS.md`.

Example:

```md
## Baton Workflow

Use `tools/baton/bin/baton` for role handoff and CR workflow state.

- Do not edit Baton SQLite records directly.
- Use `tools/baton/docs/agent-prompt.md` as the worker prompt for role agents.
- Use bounded waits; do not use `--timeout 0` unless explicitly requested.
- Treat `next` as a one-time queue check, not as a wait command.
- After wait timeout, repeat bounded waits while the shift remains active.
- Start a shift before long-running waits.
- Finish already-claimed work even if the shift expires.
- Do not create CRs with the same author and reviewer role.
- Ask an SM/admin role to use `cr reassign-reviewer` or `cr cancel` for stuck legacy CRs.
- Keep CR Markdown files under `docs/change-requests/` unless the user specifies another path.
```

## Prompting Agents

When asking a Codex role agent to work through Baton, include the role, the command path, and the expected wait behavior.

Minimal handoff worker prompt:

```text
Use Baton to receive and process work as the frontend role.

Use this command path:
tools/baton/bin/baton

Before waiting, start or extend your shift:
tools/baton/bin/baton shift start --role frontend

Then repeat bounded waits while the shift is active:
tools/baton/bin/baton wait --role frontend --timeout 900 --interval 3

Do not use repeated next commands as a substitute for wait, and do not stop when next reports no ready job.
Exit 2 means only that the bounded wait timed out: check the shift and run wait again while it remains active.
When work appears, re-check with next, claim it, complete only the claimed task, then finish it with concrete evidence.
After finish, return to bounded wait while the shift remains active.
Blocked handoffs are promoted automatically after their dependencies finish. Cancelled dependency chains will not become ready.
Do not edit Baton SQLite records directly.
```

Minimal CR reviewer prompt:

```text
Use Baton to review submitted CRs as the sm role.

Use this command path:
tools/baton/bin/baton

Before waiting, start or extend your shift:
tools/baton/bin/baton shift start --role sm

Then repeat bounded CR review waits while the shift is active:
tools/baton/bin/baton cr wait-review --role sm --timeout 900 --interval 3

When a CR appears, inspect the Markdown file, then approve, reject, or request revision through Baton.
If approved implementation should proceed, create implementation handoffs through Baton.
Do not edit Baton SQLite records directly.
```

Minimal revision worker prompt:

```text
Use Baton to process your assigned revision handoff as the planning role.

Claim only work targeted to planning.
If the handoff asks for CR revision, edit the CR Markdown body, run `cr resubmit`, then finish the handoff with evidence.
Do not treat `finish` as CR resubmission; CR state changes must use `cr resubmit`.
```

Short user-facing command:

```text
Use Baton as the frontend role. Start a shift, wait for work, claim the next eligible job, complete it, finish it with evidence, then re-enter bounded wait if the shift is still active.
```

## Convenience Wrapper

For easier use, add a project-local wrapper such as `scripts/baton`:

```bash
#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec "$ROOT/tools/baton/bin/baton" "$@"
```

Then run:

```bash
scripts/baton init
scripts/baton cr create --title "Upload policy" --author-role planning
```

## Shell Alias

For local interactive work, an alias is also enough:

```bash
alias baton='tools/baton/bin/baton'
```

## Updating Baton

When using a submodule:

```bash
cd tools/baton
git fetch --tags
git checkout v0.2.0
cd ../..
git add tools/baton
git commit -m "Update Baton to v0.2.0"
```

When using a plain clone:

```bash
cd tools/baton
git fetch --tags
git checkout v0.2.0
```

After changing Baton versions, run the database migration command from the consuming project root before starting agents:

```bash
tools/baton/bin/baton migrate
tools/baton/bin/baton role permission-list sm
```

`migrate` applies pending migrations in one transaction, records them in `schema_migrations`, validates database and foreign-key integrity, and seeds newly introduced default roles or permissions. It does not rewrite existing handoff, CR, event, control, role, or permission content. Re-running it is safe.

If migration fails, Baton rolls back the transaction and leaves the previous database records in place. A Baton binary also refuses to open a database containing migration versions it does not recognize, which prevents an older checkout from modifying a newer database.

Database-backed `baton` commands apply pending migrations automatically, but run `migrate` explicitly during an upgrade so failures are found before role agents start. `baton-report` is read-only and requires the migration to be completed first.

The old `update` command remains a deprecated migration alias for v0.1.6 compatibility. New scripts must use `migrate`.

## When To Avoid Sharing One Database

Do not share one `.baton/baton.sqlite3` across unrelated repositories. Baton's IDs, CR file paths, and handoff source references are repository-local.

Use a separate Baton database per project unless the user explicitly wants one shared workflow across multiple repositories.
