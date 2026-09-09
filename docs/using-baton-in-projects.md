# Using Baton In Other Projects

English (primary) | [한국어 안내](../README.ko.md)

This guide explains how to add Baton to another repository as a local workflow tool.

## Recommended Layout

For one checkout, keep Baton control data inside the consuming project:

```text
your-project/
├── .baton/
│   ├── .gitignore
│   ├── change-requests/
│   ├── project.json
│   └── baton.sqlite3
├── baton.toml
├── tools/baton/
└── AGENTS.md
```

- `.baton/project.json`: project boundary marker used by both installed commands.
- `.baton/baton.sqlite3`: project-local workflow authority.
- `.baton/change-requests/`: branch-independent, editable CR bodies created by default.
- `.baton/.gitignore`: generated protection that keeps control data out of Git.
- `baton.toml`: optional shared Baton version and Git workspace policy.
- `tools/baton/`: Baton repository, usually as a submodule.
- `AGENTS.md`: project-specific agent rules.

When implementation agents use isolated Git worktrees, put one Baton control root outside
those checkouts and point every agent at the same database:

```text
your-project-control/
├── .baton/
│   ├── change-requests/
│   ├── project.json
│   └── baton.sqlite3
├── baton.toml
└── worktrees/
    ├── integration/
    ├── task-HO-001/
    └── task-HO-002/
```

Use one control root per logical project. Do not run `baton init` independently in each
worktree; that creates split workflow databases. The control root does not need to be a
Git checkout.

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
baton project info
baton role list
```

The installation location does not select the Baton database. `baton init` makes its current directory the project root and creates `.baton/project.json`; use `baton init --project-root PATH` when initializing another directory. Later commands walk upward to the nearest marker. Git metadata is never consulted, so nested execution, directory moves, and full project copies retain their project-local DB selection.

The installed command provides two discovery interfaces:

```bash
baton help project migrate
baton guide show bootstrap
```

Use `help` or `-h` for command syntax and options. Use `guide` for version-matched agent operating policy.

An existing `.baton/baton.sqlite3` without a marker is discovered while walking ancestors. `baton init` adopts a current in-place database by adding only the marker; `baton migrate` adds the marker after a required schema upgrade. If the database is in a legacy nested tool layout, first use `baton project migrate --check`. If discovery fails, use `--source-db /path/to/existing/baton.sqlite3`; an explicit path is checked but never applied automatically. Apply only after reviewing the plan and passing its `plan_token` to `baton project migrate --apply` with the same path options.

For a package-index installation, upgrade the user-level command with pipx:

```bash
cd /path/to/each-active-project
baton stop --all --reason "Baton upgrade"
baton upgrade preflight
pipx upgrade agents-baton
```

Run the read-only preflight with the currently compatible Baton before replacing the executable. It is ready only after a global stop and after all waiter leases, active handoffs, cancellation acknowledgements, and claimed submitted CR reviews are drained. It reports concrete blocker IDs when work remains.

For a Git URL installation, explicitly replace it with the chosen new tag, then migrate each active project database:

```bash
pipx install --force "git+https://github.com/karl27hg/agents-baton.git@vNEW.VERSION"
cd /path/to/your-project
baton migrate
baton migrate --check
baton resume --all
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

Baton defaults to this database path when run under a discovered Baton project root:

```text
.baton/baton.sqlite3
```

The default local agent identity file is:

```text
.baton/agent-id
```

For isolated worktrees, set the shared control database and the source checkout explicitly:

```bash
export BATON_DB=/absolute/path/to/your-project-control/.baton/baton.sqlite3
export BATON_WORKSPACE_ROOT="$PWD"
baton project info
baton workspace check
```

`BATON_DB` is equivalent to the global `--db` option. `BATON_WORKSPACE_ROOT` selects the
Git checkout inspected by optional VCS policy and defaults to the current directory. Use
a distinct `BATON_AGENT_ID` for each active agent profile sharing the database.

An arbitrary external database remains useful for diagnostics or isolated testing:

```bash
tools/baton/bin/baton --db /tmp/baton.sqlite3 init
```

An external database at `<control-root>/.baton/baton.sqlite3` resolves files against that
control root. Arbitrarily named external databases have no implicit project root, so use
absolute CR file paths with them. `init --project-root` always uses that root's canonical
database and cannot be combined with a different external DB.

## Git Ignore

`baton init` creates `.baton/.gitignore` with `*`, so new control data and mutable CRs are
not added to Git accidentally. A repository-level ignore remains acceptable:

```gitignore
.baton/
*.sqlite3
*.sqlite3-shm
*.sqlite3-wal
```

Do not place the mutable CR body in a task branch. If an approved requirement must become
part of repository history, commit a reviewed snapshot to the integration branch and refer
to its immutable commit SHA; the shared `.baton/change-requests/` file remains the Baton
workflow artifact.

## CR Body Integrity

SQLite remains authoritative for CR workflow state, while the shared Markdown file is the
editable body. Baton hashes only the body, excluding managed frontmatter:

1. `cr submit` and `cr resubmit` record the exact submitted body hash.
2. `cr approve` refuses approval if the body changed after submission.
3. Approval records an immutable approved body hash.
4. `cr create-handoff`, implementation `claim`, `finish`, and `cr mark-implemented` refuse
   to proceed if the approved body is missing or changed.
5. `cr status` and `cr show` report `ok`, `mismatch`, `missing`, `unreadable`,
   `editable`, or `legacy-unsealed` integrity.

Use `baton cr show CR-ID` from any worktree to resolve the shared absolute path and read the
body. After approval, restore an accidental edit from the reviewed copy or create a new CR;
do not silently reseal changed requirements. Schema migration does not guess hashes for
already approved CRs. Their assigned reviewer must explicitly seal the current legacy body:

```bash
baton cr seal CR-YYYY-MM-DD-001 \
  --role sm \
  --evidence "Verified legacy approved body before implementation."
```

New implementation and revision handoffs use the stable `cr:CR-ID` source reference instead
of a branch-relative file path. General design documents remain Git artifacts and should use
an immutable reference such as `<commit-sha>:docs/design.md`.

When a cancelled implementation handoff is replaced under the same approved CR, the reviewer records the explicit relationship with `cr supersede-handoff`. Migration never infers this relationship from old cancellations. `cr mark-implemented` accepts the retired handoff only when its audited replacement chain reaches a finished implementation.

## AGENTS.md

Add project-specific Baton rules to the consuming project's `AGENTS.md`.

For a pipx installation, use a short bootstrap that reads instructions bundled with the installed Baton version:

```md
## Baton Workflow

Use `baton` from `PATH` for role handoff and CR workflow state.

- Before project setup or migration, read `baton guide show bootstrap`.
- Before worker or reviewer operation, read `baton guide show worker`.
- Before decomposing or registering parallel work, read `baton guide show planner`.
- When `baton.toml` enables Git integration, read `baton guide show git` and run `baton workspace check`.
- Do not create subagents, child tasks, parallel agent sessions, or delegated background agents while operating under Baton.
- Delegate work only through Baton handoffs assigned to configured roles.
- Keep roles as permission boundaries. Use workstreams only when agents in one broad role have different domain ownership.
- Give each concrete agent at most one active handoff or CR review claim.
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
- Do not create subagents, child tasks, parallel agent sessions, or delegated background agents while operating under Baton.
- Delegate work only through Baton handoffs assigned to configured roles. A planner may register safe parallel handoffs but must not directly invoke their workers.
- Keep roles as permission boundaries. Register stable domain workstreams such as `api-contract` or `ui-regression` when one broad role contains distinct specialists.
- At integration fan-in, run independent evidence checks in parallel workstreams and register one final handoff depending on every required result. Do not parallelize the final merge or release decision.
- A concrete agent owns at most one active handoff or submitted CR review. A workstream-routed reviewer must use `cr claim-review`; use `cr release-review --reason ...` before yielding undecided work.
- Use `tools/baton/docs/planner-prompt.md` for agents that decompose or register parallel work.
- Use `tools/baton/docs/agent-prompt.md` as the worker prompt for role agents.
- Treat work as parallel only after confirming independent inputs, write sets, contracts, shared state, and completion order; otherwise declare a dependency or Gate.
- Use bounded waits; do not use `--timeout 0` unless explicitly requested.
- Keep the default automatic interval unless the user or project policy requires a fixed numeric interval.
- Treat `next` as a one-time queue check, not as a wait command. Use `next --explain` to diagnose role, workstream, or ownership exclusions.
- After wait timeout, repeat bounded waits while the shift remains active.
- Do not report ordinary wait timeouts or unchanged waiting state; report actual state transitions once.
- Start a shift before long-running waits.
- Finish already-claimed work even if the shift expires.
- If claimed work cannot satisfy its exit criteria, use lifecycle `fail`; the linked failure CR keeps downstream work blocked until a reviewed retry finishes or the branch is cancelled. A completed validation may instead use `finish --outcome fail|conditional|inconclusive --blocking`.
- Treat handoff dependencies as after-completion edges. Put success-dependent implementation behind a Gate and inspect structured completion outcomes before releasing it.
- Explicit `finish --commit` values must resolve locally unless an audited unresolved override is intentional. Correct wrong finished commit evidence with `handoff evidence-correct`, never by editing SQLite.
- Do not create CRs with the same author and reviewer role.
- Ask an SM/admin role to use `cr reassign-reviewer` or `cr cancel` for stuck legacy CRs.
- Configure least privilege with `role permission-add` and `role permission-remove`; do not edit permission rows directly.
- Grant `handoff.register` only to roles allowed to create or retry work. Schema v7 preserves this formerly implicit access for existing roles, so review compatibility grants after migration.
- Use handoff `cancel` only with explicit user/SM intent and a role granted `handoff.cancel`.
- An `in_progress` cancellation becomes `cancel_requested`. The claimant pauses before commit or integration and inspects the request reason in `events`. If review finds no issue, a role with `handoff.cancel` uses `cancel-withdraw --reason ...`, preserving the existing claim; the claimant resumes only after `handoff show` returns to `in_progress`. Retired CR implementation work cannot be restored. If cancellation is confirmed, the claimant runs `cancel-ack` with evidence; use `cancel --force` only when acknowledgement is impossible.
- Handoff cancellation affects only the selected job and its blocked dependency descendants; unrelated queues remain active.
- Use a named Gate when work must wait for a future stage whose handoff ID does not exist yet.
- Treat Gate release as a workflow decision requiring evidence, not as a routine worker action.
- Use `gate transfer` only for an explicit ownership change or emergency recovery.
- Read CR handoff references such as `cr:CR-...` with `baton cr show <cr-id>`; do not resolve them relative to a task worktree.
- Keep mutable CR Markdown under the shared `.baton/change-requests/` directory unless the user specifies an absolute branch-independent path.
- Treat `body_integrity: mismatch` as a stop condition. Do not implement or finish work against a changed approved body.
- Replace an incompatible approved design with `cr supersede`; do not edit the approved body. Compatible changes keep valid work, while supersession retires linked unfinished implementation work.
- After migrating an existing approved CR without a hash, ask its assigned reviewer to run `cr seal` before creating or claiming implementation work.
```

## Prompting Agents

When asking a Codex role agent to work through Baton, include the role, the command path, and the expected wait behavior.

Minimal handoff worker prompt:

```text
Use Baton to receive and process work as the frontend role.

Do not create subagents or child tasks. Delegate only through Baton handoffs assigned to configured roles.

Use this command path:
tools/baton/bin/baton

Before waiting, inspect your shift:
tools/baton/bin/baton shift status --role frontend

If no applicable deadline exists and no role/global scope is stopped or expired, start the default 4-hour role shift:
tools/baton/bin/baton shift start --role frontend

Preserve an active deadline. Do not restart, extend, or resume an expired or stopped scope without explicit user or SM authorization.

Then repeat bounded waits while the shift is active:
tools/baton/bin/baton wait --role frontend --timeout 900

Do not use repeated next commands as a substitute for wait, and do not stop when next reports no ready job.
Exit 2 means only that the bounded wait timed out: check the shift and run wait again silently while it remains active.
Do not send periodic or duplicate waiting updates. Report once when work becomes ready, a claim or completion changes state, waiting stops or the shift expires, an error needs intervention, or the user asks for status.
When work appears, re-check with next, claim it, complete only the claimed task, then finish it with concrete evidence and an explicit completion outcome.
Before commit, integration, or finish, inspect the handoff again. If it is `cancel_requested`, pause and inspect `events`; resume only after an authorized `cancel-withdraw` restores `in_progress`, otherwise use `cancel-ack` with evidence once cancellation is confirmed instead of `finish` or `fail`.
If the exit criteria cannot be met, report it with baton fail and the available evidence. If an assigned validation completed and found a problem, finish it with a non-pass outcome and `--blocking` instead of treating the validation execution as failed.
After finish or failure reporting, return to bounded wait while the shift remains active.
Blocked handoffs are promoted automatically after their dependencies finish. This is after-completion, not after-success; success-dependent work must also wait on a planner/reviewer Gate. A lifecycle-failed dependency remains blocked pending its failure CR decision; cancelled dependency branches will not become ready, while unrelated queue branches remain active.
Do not edit Baton SQLite records directly.
```

Minimal planner/SM reviewer prompt:

```text
Use Baton to review submitted CRs as the sm role.

Do not create subagents or child tasks. Create implementation work only as Baton handoffs.

Use this command path:
tools/baton/bin/baton

Before waiting, inspect your shift:
tools/baton/bin/baton shift status --role sm

If no applicable deadline exists and no role/global scope is stopped or expired, start the default 4-hour role shift:
tools/baton/bin/baton shift start --role sm

Preserve an active deadline. Do not restart, extend, or resume an expired or stopped scope without explicit user or SM authorization.

Then repeat the combined CR and handoff watcher while the shift is active:
tools/baton/bin/baton watch --role sm --timeout 900

On exit 2, check the shift and re-enter the wait silently while it remains active. Do not report unchanged waiting state.
Do not send a final response while the shift remains active merely because one action completed or the queue is empty; Baton cannot create a new Codex turn after this one ends.
The watcher returns assigned CRs before handoffs. When a workstream-routed CR appears, claim it with `cr claim-review` before inspection, then approve, reject, request revision, or release the claim through Baton.
If approved implementation should proceed, create implementation handoffs through Baton.
Inspect `handoff list --status finished --blocking yes` and each result's `handoff show` before releasing success-dependent Gates. A finished non-pass validation satisfies ordinary dependency edges but does not authorize implementation.
If your role has direct design authority, do not create and self-review a CR. Record the authoritative contract and register implementation handoffs directly unless independent or user review is required.
When worker results require planning reconciliation, register a planning handoff depending on every required worker job, then return to watch. Make that handoff complete enough for another planning agent to claim.
When peer notification is enabled, the planner may instead remain addressable without a waiter lease only if it owns no active Baton work, has an active registered Codex session, has an explicit planning return handoff, and every producer that can release it can notify that session. Re-read Baton when messaged. Keep `watch` for CR arrival, unassigned work, incomplete notification coverage, and failed or stale delivery.
For an incompatible approved design change, use `cr supersede` with an approved replacement CR. If your planner/SM role has direct design authority and no independent review is required, use `--by-source-ref <immutable-ref>` instead. Do not modify the old approved body.
Do not edit Baton SQLite records directly.
```

Minimal revision worker prompt:

```text
Use Baton to process your assigned revision handoff as the planning role.

Do not create subagents or child tasks. Delegate only through Baton handoffs assigned to configured roles.

Claim only work targeted to planning.
If the handoff asks for CR revision, edit the CR Markdown body, run `cr resubmit`, then finish the handoff with evidence.
Do not treat `finish` as CR resubmission; CR state changes must use `cr resubmit`.
```

Short user-facing command:

```text
Use Baton as the frontend role. Do not create subagents or child tasks; delegate only through Baton handoffs. Start a shift, wait for work, claim the next eligible job, complete it, finish it with evidence, then re-enter bounded wait if the shift is still active.
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

Before replacing Baton, stop and drain each active project with the still-compatible executable:

```bash
tools/baton/bin/baton stop --all --reason "Baton upgrade"
tools/baton/bin/baton upgrade preflight
```

`upgrade preflight` is read-only and can inspect a recognized older schema. It requires the explicit global stop and lists active waiter, handoff, cancellation, and claimed-review blocker IDs. Transition those blockers with the old executable; a newly installed executable may diagnose an older DB but normal workflow commands will reject it until migration.

After changing Baton versions, run the database migration command from the consuming project root before starting agents:

```bash
tools/baton/bin/baton migrate
tools/baton/bin/baton migrate --check
tools/baton/bin/baton --version
tools/baton/bin/baton role permission-list sm
tools/baton/bin/baton resume --all
```

`migrate` applies pending migrations in one transaction, records them in `schema_migrations`, validates database and foreign-key integrity, and seeds newly introduced default roles or permissions. It does not rewrite existing handoff, CR, event, control, role, or permission content. Permissions removed with `role permission-remove` remain revoked across later migrations unless a migration explicitly introduces that same permission as a new default. Migration 7 is the deliberate exception for the newly explicit `handoff.register`: it grants the permission to existing active roles so an upgrade does not silently remove their former ability to register work. Review and revoke those compatibility grants after migration when the project requires centralized registration. Re-running `migrate` is safe.

Schema v13 adds structured completion outcomes and append-only commit-evidence corrections. Existing finished jobs remain intact with `completion_outcome=unspecified`, and old commit references are marked `legacy_unchecked`. It grants the new `handoff.evidence_correct` permission to `planning` and `sm`. Use the compatibility booleans from `project info` or `upgrade preflight`; the last package version recorded in metadata is diagnostic and does not decide compatibility.

If migration fails, Baton rolls back the transaction and leaves the previous database records in place. A Baton binary also refuses to open a database containing migration versions it does not recognize, which prevents an older checkout from modifying a newer database.

Database-backed workflow commands never apply pending migrations automatically. They reject an old schema until an operator runs `migrate`; pending migrations create a validated backup under the database's `backups/` directory before the transaction begins. `migrate --check` is read-only and verifies that no migrations remain pending. `baton-report` is read-only and requires the migration to be completed first.

The old `update` command remains a deprecated migration alias for v0.1.6 compatibility. New scripts must use `migrate`.

When moving from a nested source checkout to a pipx-installed command, inspect the database path before the normal schema migration:

```bash
baton project migrate --check
baton project migrate --apply --plan-token <token>
baton migrate --check
```

Automatic discovery recognizes `.baton/baton.sqlite3`, `tools/baton/.baton/baton.sqlite3`, and `tools/agents-baton/.baton/baton.sqlite3` under the selected Baton project root. Use `--project-root PATH` when no marker exists yet or the command runs outside the intended project, or `--source-db PATH` when the existing database is elsewhere. Check mode performs the real migration logic only on an in-memory clone. Apply mode rechecks the source signature, blocks active waiters and in-progress or cancel-requested handoffs, requires `stop --all` for a layout move, backs up the source, installs the marker, and refuses to overwrite or merge a different existing target database. A successful layout move replaces the legacy path with a symlink to the canonical database to prevent old wrappers from creating a split workflow.

## Opt-In Codex Peer Notification

The installed CLI does not call Codex or hold Codex credentials. Existing Codex tasks opt in by recording their stable profile, role, thread ID, and model with `agent session-set`. The thread and model are runtime metadata only; Baton permissions and claims continue to use role and stable `agent_id`.

`finish` immediately opens eligible direct dependents. Any agent may inspect direct downstream state with `handoff successors <finished-job>`; the reported role is eligibility, and `unassigned` never means the inspecting agent owns the work. Only `claim` establishes the concrete worker.

After a handoff finishes, its agent can optionally run `notify targets <finished-job> --role <role> --from-agent <profile>` to list existing Codex peer candidates, ranked by recent session update. A project-local global or target-role stop, including shift expiry, returns `outside_shift` instead of a candidate; the handoff stays `open` and no host message should be sent. The agent sends one `candidate` a host follow-up containing the receiving handoff ID, then records the result with `notify record --status sent|failed`. The stored `sent` value is displayed as `host_accepted` because it proves host acceptance, not recipient acknowledgement or ownership. Use `notify status <ready-job> --stale-after 15m` to distinguish accepted-unclaimed, stale-unclaimed, and claimed outcomes. Successful delivery is unique per handoff attempt; reviewed retry increments the attempt so its corrected baseline can produce a new audit record. The receiver must inspect and claim the handoff before editing.

Registration itself is the opt-in switch; projects that do not register sessions retain the existing polling behavior. Baton does not assume that other models or hosts provide a compatible task-message protocol. Keep `wait`/`watch` for CR monitoring, unassigned role queues, inaccessible or stale tasks, failed messages, and non-Codex environments. A planner may be addressable-idle without a waiter only when it owns no active Baton work, has an explicit return handoff, and every releasing producer can notify its active session; it must re-read Baton on wake-up. Do not create new Codex tasks as part of this flow. End stale endpoints explicitly, and use `--replace` only after verifying the replacement task. Baton stores no host token or message body.

### Known Fan-In Limitation

Notification candidate selection is advisory and does not reserve an agent. It excludes profiles that own an `in_progress` or `cancel_requested` handoff or a claimed submitted CR review. Parallel finishers can still select the same idle profile before the first notified handoff is claimed.

Treat incoming messages as queue wake-ups, never as preemption:

1. Continue the currently claimed handoff or CR review; do not replace its context with the newest message.
2. After the current unit reaches a safe Baton transition, run `watch` or `next` again.
3. Re-open the referenced handoff or CR from Baton because the message may now be stale.
4. Start a handoff only after `claim` succeeds. A message alone grants no ownership.

At a planned convergence point, prefer one fan-in handoff with every branch listed through `--depends-on`. When completion also requires an explicit review decision, place that handoff behind a Gate. This produces one readiness transition and one optional wake-up after all branches finish. If genuinely independent review jobs are required, leave them in the role queue and let the reviewer claim them serially.

This remains an operational limitation until Baton has an atomic, expiring dispatch reservation and a concrete CR-review claim. Do not assume `candidate` means the recipient is still idle at message-delivery time.

## When To Avoid Sharing One Database

Do not share one `.baton/baton.sqlite3` across unrelated repositories. Baton's IDs, CR file paths, and handoff source references are repository-local.

Use a separate Baton database per project unless the user explicitly wants one shared workflow across multiple repositories.

A pipx-installed executable is shared by all projects for the same OS user, but each Baton marker has its own database, controls, waiter leases, CR paths, and ID sequence. Concurrent agents in different projects do not contend on SQLite unless an explicit `--db` path points them at the same file. A pipx upgrade still changes the executable for every project, so run preflight for every active project before replacement and migrate each project when it is next used. Baton does not maintain a global registry or claim to discover every moved, copied, or dormant project.

Moving the whole project preserves the marker-to-DB relationship. Copying the whole project, including `.baton/`, copies workflow history into a physically independent DB; the copies may contain identical local handoff IDs without racing. A source-control clone that excludes `.baton/` is a new Baton project and must be initialized explicitly.

Keep every active database on a local filesystem. Do not use a network mount, cloud-synchronized folder, or one DB shared across machines as a coordination service; Baton relies on the local SQLite locking model.

## Optional Git Workspace Policy

Baton does not require Git. Projects that want checkout diagnostics may commit a root `baton.toml` while continuing to ignore `.baton/`:

```toml
[baton]
required_version = ">=0.6.0.dev0,<0.7"

[vcs]
provider = "git"
policy = "warn"
```

No config means `off`; a Git provider without a policy defaults to `warn`. Use `baton workspace check` before and after an intentional checkout and `baton workspace events` or `baton-report audit` for provenance. Do not enable `strict` until the project's branch, rebase, and cherry-pick practices have been exercised under `warn`.

See `docs/git-integration.md` for the complete mode semantics, recorded fields, ancestry rules, strict override, checkout procedure, and limitations.
