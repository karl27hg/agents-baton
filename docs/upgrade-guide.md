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

## Current Release: 0.6.0

Baton 0.6.0 upgrades project databases from schema v14 to v15. Keep the project stopped, run the normal preflight before replacing the executable, and run `baton migrate` separately in every project before resuming agents. The additive migration preserves all workflow rows, backfills each existing non-null `handoff_jobs.outcome_cr_id` as the first ordered outcome link, and grants the newly introduced `notification.observe` permission only to `sm` and `planning`.

Changes from RC9:

- Repeat `finish --outcome-cr <CR-ID>` to record every CR associated with one completed result. The first value remains available through the legacy `outcome_cr_id` field.
- Use `handoff outcome-cr-link <JOB> --cr <CR-ID> --role <ROLE> --reason <TEXT>` only when an authorized reviewer discovers another blocker after completion. It requires `handoff.evidence_correct`, appends a relationship and audit event, and never rewrites earlier links.
- `status`, `handoff show`, `handoff list`, and `baton-report summary` now evaluate every linked outcome CR. A blocking result is resolved only when all linked CRs are `implemented`.
- Use `notify observe` only for a verified terminal receiver-side result after a host-accepted notification. The command requires `notification.observe`, stores only fixed result and reason-class values, and does not modify delivery, recovery, claim, or handoff state.

Examples:

```bash
baton finish HO-... --role qa --evidence "..." --outcome fail --blocking \
  --outcome-cr CR-...-001 --outcome-cr CR-...-002
baton handoff outcome-cr-link HO-... --cr CR-...-003 \
  --role planning --reason "Independent blocker found during review."
baton notify observe HO-... --notification 42 --role planning \
  --result execution_failed --reason-class policy_blocked
```

Allowed observation results are `execution_failed`, `execution_cancelled`, and `recipient_unreachable`. Allowed reason classes are `policy_blocked`, `host_error`, `timeout`, `cancelled`, and `unknown`. Do not put raw host errors, prompts, or secrets in Baton; this command intentionally has no free-form detail option. One notification accepts at most one observation.

## Stable Monitoring

The stable release passed the complete automated suite, including isolated pipx lifecycle, schema v14-to-v15 migration, multi-project isolation, multi-CR blocker projections, and bounded notification observations. Continue monitoring representative projects for data loss, migration or compatibility failure, dependency-order inversion, duplicate claim, CR integrity failure, incorrect blocker resolution, conflicting observation records, or another state-safety defect. Apply future fixes through a patch release rather than moving the `v0.6.0` tag.

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
- Repeat `finish --outcome-cr` for every known blocker; only planning/SM may append a later blocker link under `handoff.evidence_correct`.
- Only planning/SM records verified receiver-side failures with `notify observe`; use fixed classifications and never store raw host errors.
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
