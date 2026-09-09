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

## Current Release: 0.6.0rc8

RC8 upgrades schema v13 to v14. The migration is additive and preserves existing notification, handoff, CR, role, control, and audit rows. Before changing the database, `baton migrate` creates and validates a project-local backup.

Changes from RC7:

- Notification rows now have a monotonic `delivery_attempt` within each handoff attempt. Existing rows are numbered in ID order during migration.
- `notify candidates <open-handoff>` finds eligible peer sessions without requiring a predecessor edge. Do not add a false dependency solely to create a notification path.
- A successful `notify record` now rejects a target role that is stopped or outside its shift.
- After an ordinary host-accepted result, further `notify record` calls for that handoff attempt are rejected; stale recovery must use `notify retry`.
- `notify status` retains the compatible `notification_state` and adds factual `notification_context`, latest delivery ID/attempt, and recovery count fields.
- `notify retry` records at most one same-recipient recovery delivery for an open, unclaimed handoff after the latest host-accepted notification becomes stale.

`notify retry` does not send a host message. Send the recovery follow-up to the same registered recipient task first, then record the real result. The original recipient session must still be active, the target role must be inside its project-local shift, and the recipient must still have capacity. A failed recovery record also consumes the one recovery allowance; continue with `wait` or `watch` instead of broadcasting repeatedly.

Example:

```bash
baton notify status HO-READY --stale-after 15m

# Send one recovery follow-up to the same registered Codex task, then record it.
baton notify retry HO-READY \
  --notification 42 \
  --role planning \
  --from-agent planner-main \
  --status sent \
  --reason stale_unclaimed \
  --stale-after 15m \
  --message-ref <host-message-id>
```

Host acceptance is not recipient acknowledgement. Only a successful Baton `claim` acknowledges ownership. Recovery remains an optional Codex transport optimization; other hosts and unavailable endpoints continue through the persistent `next`, `wait`, or `watch` path.

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
