# Agent Usage: SQLite Baton Prototype

This document is for agents testing the SQLite Baton prototype.

Do not use this prototype to operate the live repository handoff queue unless the user explicitly asks for an experiment.

## Agent Rules

- Use `baton-sqlite/baton`, not raw SQL, for normal operations.
- Use `--db` with a temporary database when testing.
- Use `AGENT-PROMPT.md` when running as a wait worker.
- Use a stable profile name as the primary agent identity.
- Claim only jobs targeted to your role.
- Finish only jobs that you have claimed for your role.
- Record concrete evidence when finishing.
- Do not treat prototype DB state as active project workflow state.

## Minimal Agent Flow

```bash
baton-sqlite/baton --db /tmp/baton.sqlite3 init
baton-sqlite/baton --db /tmp/baton.sqlite3 next --role frontend
baton-sqlite/baton --db /tmp/baton.sqlite3 claim HO-YYYY-MM-DD-001 --role frontend
baton-sqlite/baton --db /tmp/baton.sqlite3 finish HO-YYYY-MM-DD-001 --role frontend --evidence "Evidence summary"
```

## Creating Downstream Work

Any role may register downstream work when it has a valid source reference and does not expand product scope.

```bash
baton-sqlite/baton --db /tmp/baton.sqlite3 register \
  --title "Backend follow-up" \
  --role backend \
  --source-ref "docs/change-requests/CR-YYYY-MM-DD-example.md" \
  --objective "Apply the approved API contract." \
  --exit-criteria "Backend behavior matches the approved API contract."
```

Use `--depends-on` for sequential work:

```bash
baton-sqlite/baton --db /tmp/baton.sqlite3 register \
  --title "QA regression" \
  --role qa \
  --depends-on HO-YYYY-MM-DD-001 \
  --objective "Verify the completed implementation." \
  --exit-criteria "QA evidence is recorded."
```

## Role Configuration

Agents can test role configuration in prototype databases:

```bash
baton-sqlite/baton --db /tmp/baton.sqlite3 role add content-design --display-name "Content Design"
baton-sqlite/baton --db /tmp/baton.sqlite3 role alias-add cd content-design
baton-sqlite/baton --db /tmp/baton.sqlite3 next --role cd
```

CR review authority is configured with role permissions. `sm` is seeded with all CR permissions.

```bash
baton-sqlite/baton --db /tmp/baton.sqlite3 role permission-list sm
baton-sqlite/baton --db /tmp/baton.sqlite3 role permission-add architecture cr.review
baton-sqlite/baton --db /tmp/baton.sqlite3 role permission-add architecture cr.approve
```

Do not change active project roles based on prototype results without SM/user approval.

## CR Author Flow

CR Markdown is the editable request body. Baton owns workflow state and synchronizes only the Markdown frontmatter.

```bash
baton-sqlite/baton --db /tmp/baton.sqlite3 cr create \
  --title "Upload policy" \
  --author-role planning \
  --reviewer-role sm

baton-sqlite/baton --db /tmp/baton.sqlite3 cr submit CR-YYYY-MM-DD-001 --role planning
```

When a reviewer requests revision, claim the generated handoff, edit the CR Markdown body, resubmit the CR, then finish the handoff.

```bash
baton-sqlite/baton --db /tmp/baton.sqlite3 claim HO-YYYY-MM-DD-001 --role planning
baton-sqlite/baton --db /tmp/baton.sqlite3 cr resubmit CR-YYYY-MM-DD-001 \
  --role planning \
  --evidence "Acceptance criteria clarified."
baton-sqlite/baton --db /tmp/baton.sqlite3 finish HO-YYYY-MM-DD-001 \
  --role planning \
  --evidence "CR resubmitted."
```

`finish` does not resubmit a CR. The CR state transition must use `cr resubmit`.

## CR Reviewer Flow

Reviewer roles need `cr.review` plus the action-specific permission such as `cr.approve` or `cr.request_revision`.

```bash
baton-sqlite/baton --db /tmp/baton.sqlite3 cr wait-review --role sm --timeout 900 --interval 30
```

Possible review actions:

```bash
baton-sqlite/baton --db /tmp/baton.sqlite3 cr request-revision CR-YYYY-MM-DD-001 \
  --role sm \
  --reason "Acceptance criteria is unclear."

baton-sqlite/baton --db /tmp/baton.sqlite3 cr approve CR-YYYY-MM-DD-001 \
  --role sm \
  --evidence "Ready for implementation."

baton-sqlite/baton --db /tmp/baton.sqlite3 cr reject CR-YYYY-MM-DD-001 \
  --role sm \
  --reason "Out of scope."
```

Approval does not automatically assign implementation. Use a separate implementation handoff when the reviewer decides work should proceed.

```bash
baton-sqlite/baton --db /tmp/baton.sqlite3 cr create-handoff CR-YYYY-MM-DD-001 \
  --by-role sm \
  --role frontend \
  --title "Implement upload policy UI" \
  --objective "Implement the approved upload policy UI." \
  --exit-criteria "UI behavior matches the approved CR."

baton-sqlite/baton --db /tmp/baton.sqlite3 cr mark-implemented CR-YYYY-MM-DD-001 \
  --role sm \
  --evidence "Implementation handoffs finished."
```

## Agent Identity

Use a stable profile name before claiming work:

```bash
baton-sqlite/baton --db /tmp/baton.sqlite3 agent init --role frontend --agent-id frontend-main
baton-sqlite/baton --db /tmp/baton.sqlite3 agent show
```

Recommended profile names:

```text
sm
frontend-main
backend-main
qa-regression
ui-design-main
backend-design-main
devops-main
architecture-main
```

Do not rely on Codex thread IDs, turn IDs, or temporary files as the only long-lived identity.

The CLI uses identity in this order when claiming:

1. `--claimed-by`
2. `BATON_AGENT_ID`
3. `--agent-id-file` or `BATON_AGENT_ID_FILE`
4. role name

If multiple agents share one workspace, prefer explicit `--claimed-by <profile-name>` or `BATON_AGENT_ID=<profile-name>` so they do not accidentally share one local identity file.

## Wait Usage

Use bounded waits by default:

```bash
baton-sqlite/baton --db /tmp/baton.sqlite3 wait --role frontend --timeout 900 --interval 30
baton-sqlite/baton --db /tmp/baton.sqlite3 cr wait-review --role sm --timeout 900 --interval 30
```

Avoid `--timeout 0` unless the user explicitly asks for a forever-wait experiment. For normal worker operation, repeat bounded waits while the role shift is active.

## Shift Usage

Set a maximum operating time before long-running worker loops:

```bash
baton-sqlite/baton --db /tmp/baton.sqlite3 shift start --role frontend --duration 8h
baton-sqlite/baton --db /tmp/baton.sqlite3 shift extend --role frontend --duration 1h
baton-sqlite/baton --db /tmp/baton.sqlite3 shift status --role frontend
```

When a shift expires, Baton stops future waits and claims for that scope. Finish already-claimed work normally, then check `shift status` before re-entering a wait loop.

## Stop And Resume

Wait loops stop when SM or the user sets a control flag:

```bash
baton-sqlite/baton --db /tmp/baton.sqlite3 stop --role frontend --reason "Pause frontend"
baton-sqlite/baton --db /tmp/baton.sqlite3 stop --all --reason "End of day"
```

Resume before starting new waits:

```bash
baton-sqlite/baton --db /tmp/baton.sqlite3 resume --role frontend
baton-sqlite/baton --db /tmp/baton.sqlite3 resume --all
```

Check control state:

```bash
baton-sqlite/baton --db /tmp/baton.sqlite3 control status
```

Stopping wait loops must not be treated as cancelling handoff jobs. It only controls whether waiting agents should keep polling.

`resume` clears manual stops. If the shift has expired, extend or restart the shift before starting another wait.
