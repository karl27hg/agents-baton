# Using Baton In Other Projects

English (primary) | [한국어 안내](../README.ko.md)

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

- pipx installed from a release tag: fastest user-level setup and command access from any project, but the consuming repository does not record the selected version.
- Git submodule pinned to a release tag: best when the consuming project should record the exact Baton version.
- Git clone plus tag checkout: acceptable for local-only use, but the consuming project will not record the expected Baton revision unless you document it separately.
- Release archive download: useful for one-off installation, but harder to update consistently than a submodule.

Plain `git clone` is fine for experimentation. For stable use across projects, use a published release tag and update intentionally when a new Baton release is chosen.

## Install Baton With Pipx

Use pipx when one user should run `baton` from multiple local projects without keeping a Baton checkout inside each project.

From a local Baton checkout containing `pyproject.toml`:

```bash
cd /path/to/agents-baton
pipx install .
```

From a published release tag containing the packaging metadata:

```bash
pipx install "git+https://github.com/karl27hg/agents-baton.git@vX.Y.Z"
```

Then change to the consuming project before initializing or operating Baton:

```bash
cd /path/to/your-project
baton --version
baton guide show bootstrap
baton init
baton migrate --check
baton role list
```

The installation location does not select the Baton database. The current working directory at command execution selects the default `.baton/baton.sqlite3` path.

Do not run `baton init` when a project used Baton previously but the default database is absent. First use `baton project migrate --check` to discover and rehearse migration from supported legacy tool layouts. If discovery fails, use `--source-db /path/to/existing/baton.sqlite3`; an explicit path is checked but never applied automatically. Apply only after reviewing the plan and passing its `plan_token` to `baton project migrate --apply` with the same path options.

For a package-index installation, upgrade the user-level command with pipx:

```bash
pipx upgrade agents-baton
```

For a Git URL installation, explicitly replace it with the chosen new tag, then migrate each active project database:

```bash
pipx install --force "git+https://github.com/karl27hg/agents-baton.git@vNEW.VERSION"
cd /path/to/your-project
baton migrate
baton migrate --check
```

Remove the managed commands and isolated environment with `pipx uninstall agents-baton`. Uninstall leaves every consuming project's `.baton/` directory and workflow database intact. Prefer a submodule when the consuming repository must pin Baton in version control, and do not downgrade an executable after applying a schema migration unless the target version is known to support that schema.

## Add Baton As A Submodule

Submodules are the preferred option when the consuming project should pin a Baton version.

```bash
mkdir -p tools
git submodule add git@github.com:karl27hg/agents-baton.git tools/baton
```

Pin to a release tag:

```bash
cd tools/baton
git checkout vX.Y.Z
cd ../..
git add tools/baton
git commit -m "Add Baton workflow tool"
```

Run Baton from the consuming project root:

```bash
tools/baton/bin/baton init
tools/baton/bin/baton migrate --check
tools/baton/bin/baton --version
tools/baton/bin/baton role list
tools/baton/bin/baton shift start --role frontend
tools/baton/bin/baton wait --role frontend
```

The last command uses Baton's default bounded wait settings:

```text
--timeout 900
--interval auto
```

Automatic mode targets `min(30, 3 * active waiters)` seconds across handoff and CR waiters sharing the project database. Use a numeric interval only for an explicit fixed response requirement.

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
git checkout vX.Y.Z
```

Replace `vX.Y.Z` with the selected published release tag. Record that version in the consuming project's documentation or onboarding notes.

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

For a pipx installation, use a short bootstrap that reads instructions bundled with the installed Baton version:

```md
## Baton Workflow

Use `baton` from `PATH` for role handoff and CR workflow state.

- Before project setup or migration, read `baton guide show bootstrap`.
- Before worker or reviewer operation, read `baton guide show worker`.
- Before decomposing or registering parallel work, read `baton guide show planner`.
- Do not initialize a new database when an existing Baton database may be in another path.
- Do not edit Baton SQLite records directly.
- If a Baton command, role authority, database path, or migration plan is unclear, stop and ask the user or SM.
```

The `guide` output comes from the installed package, so it follows the executable version even when no `tools/baton` checkout exists. `AGENTS.md` remains responsible for assigning the project role and requiring the guide; Baton does not modify project agent instructions automatically.

For a source checkout or submodule, use the equivalent repository-local rules:

Example:

```md
## Baton Workflow

Use `tools/baton/bin/baton` for role handoff and CR workflow state.

- Do not edit Baton SQLite records directly.
- Use `tools/baton/docs/planner-prompt.md` for agents that decompose or register parallel work.
- Use `tools/baton/docs/agent-prompt.md` as the worker prompt for role agents.
- Treat work as parallel only after confirming independent inputs, write sets, contracts, shared state, and completion order; otherwise declare a dependency or Gate.
- Use bounded waits; do not use `--timeout 0` unless explicitly requested.
- Keep the default automatic interval unless the user or project policy requires a fixed numeric interval.
- Treat `next` as a one-time queue check, not as a wait command.
- After wait timeout, repeat bounded waits while the shift remains active.
- Do not report ordinary wait timeouts or unchanged waiting state; report actual state transitions once.
- Start a shift before long-running waits.
- Finish already-claimed work even if the shift expires.
- Do not create CRs with the same author and reviewer role.
- Ask an SM/admin role to use `cr reassign-reviewer` or `cr cancel` for stuck legacy CRs.
- Configure least privilege with `role permission-add` and `role permission-remove`; do not edit permission rows directly.
- Use handoff `cancel` only with explicit user/SM intent and a role granted `handoff.cancel`.
- Handoff cancellation affects only the selected job and its blocked dependency descendants; unrelated queues remain active.
- Use a named Gate when work must wait for a future stage whose handoff ID does not exist yet.
- Treat Gate release as a workflow decision requiring evidence, not as a routine worker action.
- Use `gate transfer` only for an explicit ownership change or emergency recovery.
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
tools/baton/bin/baton wait --role frontend --timeout 900

Do not use repeated next commands as a substitute for wait, and do not stop when next reports no ready job.
Exit 2 means only that the bounded wait timed out: check the shift and run wait again silently while it remains active.
Do not send periodic or duplicate waiting updates. Report once when work becomes ready, a claim or completion changes state, waiting stops or the shift expires, an error needs intervention, or the user asks for status.
When work appears, re-check with next, claim it, complete only the claimed task, then finish it with concrete evidence.
After finish, return to bounded wait while the shift remains active.
Blocked handoffs are promoted automatically after their dependencies finish. Cancelled dependency branches will not become ready, while unrelated queue branches remain active.
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
tools/baton/bin/baton cr wait-review --role sm --timeout 900

On exit 2, check the shift and re-enter the wait silently while it remains active. Do not report unchanged waiting state.
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

## Staged Review With Markdown Artifacts

For multi-agent review, keep workflow control in Baton and detailed findings in versioned Markdown artifacts. A practical staged flow is:

```text
Sol initial scan
  -> Tera scoped deep review
  -> Sol consolidation
  -> Planning triage
  -> implementation, design, and QA handoffs
```

Store the durable review payload outside SQLite and reference it through each handoff's `source_ref`:

```text
reports/engineering-review/<review-id>/
  scope.md
  sol-initial.md
  tera-review.md
  final.md
```

Create a named Gate before downstream jobs are registered so they cannot open while later review stages are still being created dynamically:

```bash
tools/baton/bin/baton gate create planning-triage-complete \
  --role planning \
  --owner-role planning \
  --owner-role architecture

tools/baton/bin/baton register \
  --title "QA finalized review scope" \
  --role qa \
  --source-ref "reports/engineering-review/<review-id>/final.md" \
  --depends-on-gate planning-triage-complete \
  --objective "Verify the implementation selected by planning triage." \
  --exit-criteria "QA evidence covers every routed finding."
```

The reviewer stages may still use ordinary `--depends-on` links as concrete handoffs are created. Planning releases the stable Gate only after consolidation and triage are complete:

```bash
tools/baton/bin/baton gate release planning-triage-complete \
  --role planning \
  --evidence "Final review report triaged and downstream scope approved."
```

This keeps role ownership, claim safety, state, dependencies, and audit events in Baton while Git and Markdown remain the artifact data plane. If the owner becomes unavailable, a role with `gate.manage` may use `gate transfer` with an explicit reason; it must not bypass unfinished review work.

For production operating rules and failure cases, read `docs/gates.md` before enabling role agents to create or resolve Gates.

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
git checkout vX.Y.Z
cd ../..
git add tools/baton
git commit -m "Update Baton to vX.Y.Z"
```

When using a plain clone:

```bash
cd tools/baton
git fetch --tags
git checkout vX.Y.Z
```

After changing Baton versions, run the database migration command from the consuming project root before starting agents:

```bash
tools/baton/bin/baton migrate
tools/baton/bin/baton migrate --check
tools/baton/bin/baton --version
tools/baton/bin/baton role permission-list sm
```

`migrate` applies pending migrations in one transaction, records them in `schema_migrations`, validates database and foreign-key integrity, and seeds newly introduced default roles or permissions. It does not rewrite existing handoff, CR, event, control, role, or permission content. Permissions removed with `role permission-remove` remain revoked across later migrations unless a migration explicitly introduces that same permission as a new default. Re-running `migrate` is safe.

If migration fails, Baton rolls back the transaction and leaves the previous database records in place. A Baton binary also refuses to open a database containing migration versions it does not recognize, which prevents an older checkout from modifying a newer database.

Database-backed `baton` commands apply pending migrations automatically, but run `migrate` explicitly during an upgrade so failures are found before role agents start. `migrate --check` is read-only and verifies that no migrations remain pending. `baton-report` is read-only and requires the migration to be completed first.

The old `update` command remains a deprecated migration alias for v0.1.6 compatibility. New scripts must use `migrate`.

When moving from a nested source checkout to a pipx-installed command, inspect the database path before the normal schema migration:

```bash
baton project migrate --check
baton project migrate --apply --plan-token <token>
baton migrate --check
```

Automatic discovery recognizes `.baton/baton.sqlite3`, `tools/baton/.baton/baton.sqlite3`, and `tools/agents-baton/.baton/baton.sqlite3` under the selected project root. Use `--project-root PATH` when the current Git root is not the intended project, or `--source-db PATH` when the existing database is elsewhere. Check mode performs the real migration logic only on an in-memory clone. Apply mode rechecks the source signature, blocks active waiters, backs up the source, and refuses to overwrite or merge a different existing target database.

## When To Avoid Sharing One Database

Do not share one `.baton/baton.sqlite3` across unrelated repositories. Baton's IDs, CR file paths, and handoff source references are repository-local.

Use a separate Baton database per project unless the user explicitly wants one shared workflow across multiple repositories.
