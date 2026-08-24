# Named Gate Operations

English (primary) | [한국어 안내](../README.ko.md)

This guide covers the operational use and safety rules for named workflow Gates introduced in Baton v0.4.0.

A Gate is a stable workflow prerequisite. Use it when downstream work must be registered before a future dynamic stage has a handoff ID, or when a human or role must explicitly approve progression.

## Basic Flow

Create the Gate before registering dependent work:

```bash
bin/baton gate create planning-triage-complete --role planning

bin/baton register \
  --title "QA after planning triage" \
  --role qa \
  --depends-on-gate planning-triage-complete \
  --objective "Verify the finalized implementation scope." \
  --exit-criteria "QA evidence covers the planning-approved scope."
```

The dependent handoff remains `blocked` until the Gate is released:

```bash
bin/baton gate release planning-triage-complete \
  --role planning \
  --evidence "Planning triage approved the final scope."
```

Gate release and promotion of eligible handoffs are committed in one transaction. Only handoffs attached to the released Gate are evaluated by that command.

## Ownership

Without `--owner-role`, the creating role becomes the sole owner:

```bash
bin/baton gate create review-complete --role planning
```

Repeat `--owner-role` for joint ownership:

```bash
bin/baton gate create review-complete \
  --role planning \
  --owner-role planning \
  --owner-role architecture
```

When any `--owner-role` is supplied, the explicit list replaces the creator default. Include the creator role explicitly if it must remain an owner.

Only an owner may release or cancel a Gate. Ownership is role-based, not tied to an individual Codex task or agent profile.

## Emergency Transfer

A current owner or a role with `gate.manage` may replace the owner set:

```bash
bin/baton gate transfer planning-triage-complete \
  --role sm \
  --owner-role architecture \
  --reason "Planning agent is unavailable."
```

`gate transfer` replaces all current owners. Roles omitted from the new list immediately lose owner authority.

`gate.manage` is an emergency transfer permission only. It does not allow a non-owner to release or cancel the Gate. After an emergency transfer, a new owner must make and record the resolution decision.

## Multiple Requirements

A handoff may depend on existing handoffs, multiple Gates, or both:

```bash
bin/baton register \
  --title "Final QA" \
  --role qa \
  --depends-on HO-YYYY-MM-DD-001 \
  --depends-on-gate review-complete \
  --depends-on-gate planning-triage-complete \
  --objective "Verify the approved implementation." \
  --exit-criteria "All required evidence is recorded."
```

The job becomes `open` only after every handoff dependency is `finished` and every Gate dependency is `released`.

A dependency on an already-released Gate does not block a newly registered job. A dependency on an already-cancelled Gate creates the job as `cancelled`.

## Cancellation

Cancel a Gate only when the workflow stage itself will not proceed:

```bash
bin/baton gate cancel obsolete-phase \
  --role planning \
  --reason "The phase was removed from the workflow."
```

Cancellation affects direct `blocked` handoffs that require the Gate and recursively cancels their blocked handoff descendants. Independent queue branches remain unchanged.

`released` and `cancelled` are terminal Gate states. Baton does not reopen or reset a resolved Gate. Create a new Gate when the workflow decision must be represented again.

Use `stop` to pause wait loops. Do not cancel a Gate merely to pause an agent or role.

## Inspection And Audit

Inspect current owners and state:

```bash
bin/baton gate status
bin/baton gate status planning-triage-complete
```

Inspect lifecycle and ownership events:

```bash
bin/baton gate events planning-triage-complete
bin/baton-report audit --gate planning-triage-complete
bin/baton-report summary
```

Release evidence, cancellation reasons, and transfer reasons should identify the actual workflow decision or incident. Avoid generic messages such as `done`, `fixed`, or `override`.

## Upgrade Checklist

After updating from Baton v0.3.0:

```bash
bin/baton --version
bin/baton migrate
bin/baton migrate --check
bin/baton role permission-list sm
```

Schema migration v3 preserves existing handoff, CR, role, permission, control, and event data. It creates the Gate tables and grants the newly introduced `gate.manage` permission to `sm` without restoring unrelated permissions that the project previously revoked.

## Limitations And Safety Rules

- A Gate controls workflow readiness; it does not serialize jobs that modify the same files, database schema, API contract, runtime, or generated artifacts.
- After one Gate is released, several dependent jobs may become `open` together. Use explicit handoff dependencies and project conflict policy until serial execution lanes are implemented.
- Baton records role authority but does not authenticate a human user. Protect database and CLI access at the operating-system and repository level.
- Create the Gate before dependent jobs. Baton intentionally rejects references to unknown Gate names.
- Do not release a Gate because its owner is unavailable. Transfer ownership first, then let the new owner verify the workflow condition and release it with evidence.
- Do not edit Gate tables directly. Use CLI commands so transactions and audit events remain complete.

The staged review example in `docs/using-baton-in-projects.md` shows how Gates coordinate Sol review, Tera review, consolidation, Planning triage, and downstream QA while Markdown remains the durable review artifact.
