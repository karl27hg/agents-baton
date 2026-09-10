# Baton Upgrade Guide

This guide is bundled with the installed Baton package. Read it after every executable update and before resuming agents. It records release-specific behavior that may require database migration, operating-guide review, or changes to the consuming project's `AGENTS.md`.

## Safe Upgrade Sequence

Run the preflight with the currently installed executable before replacing it:

```bash
baton stop --all --reason "Baton upgrade"
baton upgrade preflight
```

Drain every reported waiter, active handoff, cancellation acknowledgement, and claimed CR review. Repeat preflight until it reports `READY`. Then update the executable and run these commands from each Baton project root:

```bash
baton --version
baton guide show upgrade
baton guide show changelog
baton migrate
baton migrate --check
baton project info
```

Review this guide, the bundled changelog entries newer than the project's previous version, and the version-matched bootstrap, worker, or planner guide before updating project instructions and explicitly resuming the applicable shifts. A shared pipx update never migrates every project automatically.

## Current Release: 0.6.0rc9

RC9 keeps schema v14 unchanged. Projects already migrated by RC8 need only `baton migrate --check`; projects upgrading from RC7 or earlier must still follow the normal migration sequence, which preserves existing notification, handoff, CR, role, control, and audit rows.

Changes from RC8:

- `notify list` now defaults to unlimited newest-first output so recent operational records appear first; use `--order oldest` for chronological history.
- `notify list --limit N` provides an explicit bounded recent audit view without silently omitting rows by default.
- `notify list --after-id ID` and `--before-id ID` provide exclusive, stable ID cursors that can be combined with ordering and limits.
- `notify list --recovery-only` selects controlled recovery deliveries without post-processing JSON.
- All new filters compose with `--job`, `--status`, and `--format json`; they do not modify notification records.

Recent recovery audit example:

```bash
baton notify list --limit 20
baton notify list --after-id 90 --recovery-only
```

Cursor boundaries are exclusive. `--after-id 90` starts at ID 91, while `--before-id 120` ends at ID 119. Use `--before-id <oldest-id-from-previous-page> --limit N` to page backward through older records. The new options affect only presentation and require no data migration.

## Stable Qualification

RC9 must run in a representative project for at least two hours before promotion to stable `v0.6.0`. Review the resulting feedback and accumulated notification history. Do not promote when operation reveals data loss, migration or compatibility failure, dependency-order inversion, duplicate claim, CR integrity failure, duplicate recovery delivery, incorrect notification filtering, or another state-safety defect. Run the complete release and documentation-gap checks again immediately before stable approval.

## Project AGENTS.md Review

Baton does not edit a consuming project's `AGENTS.md`. The SM or project owner must review it after an upgrade. At minimum, keep equivalent rules for the following:

```markdown
## Baton Workflow
- Before Baton work, run `baton --version` and read `baton guide show bootstrap`.
- Workers must follow `baton guide show worker`; planning or SM agents must also follow `baton guide show planner`.
- After a Baton update, read `baton guide show upgrade` and `baton guide show changelog`, run the required project-local migration checks, and update this file when agent behavior changed.
- Do not create subagents, child tasks, parallel agent sessions, or delegated background agents while operating under Baton. Delegate only through registered Baton handoffs.
- Treat host messages as hints. Inspect and claim the referenced Baton work before editing.
- Record every attempted host message immediately. Use only one controlled stale recovery and fall back to `wait` or `watch` afterward.
```

Project-specific role names, workstreams, approval authority, Git policy, shift authority, and notification opt-in rules still belong in the project's own instructions. Do not replace stricter project policy with this generic block.

## Release Review Checklist

After each future Baton update:

1. Compare `baton --version` with the version required by project policy.
2. Read this bundled upgrade guide, every newer changelog entry, and the relevant role guide.
3. Complete project-local migration and compatibility checks.
4. Review `AGENTS.md` for changed commands, lifecycle rules, authority, and prohibited behavior.
5. Verify one representative status, CR, handoff, and notification workflow before resuming all agents.
6. Resume only by explicit user or SM authority.

If the guide, executable, database schema, or project instructions disagree, keep the project stopped and ask the user or SM. Do not guess or silently rewrite workflow state.
