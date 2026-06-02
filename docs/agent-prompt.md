# Agent Prompt: SQLite Baton Wait Worker

Use this prompt when an agent is assigned to wait for and process handoff work through the Baton.

## Role Setup

You are a role worker. Your role is provided by the user or the surrounding thread context.

Before waiting or claiming work, use the profile name assigned by the user or project policy.

```bash
bin/baton --db <db> agent init --role <role> --agent-id <profile-name>
bin/baton --db <db> agent show
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

Do not rely on Codex thread IDs, turn IDs, or temporary files as the only long-lived identity. Thread/session IDs are runtime identifiers and may change after app restart, thread restore, or internal session recreation.

If several agents share the same workspace, do not let them unintentionally share the same default identity file. Prefer `--claimed-by <profile-name>` or `BATON_AGENT_ID=<profile-name>` when the profile name is already known.

## Waiting Rules

- Use bounded waits by default.
- Do not send periodic waiting updates while `baton wait` is running.
- Report only when `wait` returns, times out, or is stopped.
- Avoid `--timeout 0` unless the user explicitly asks for a forever-wait experiment.
- Keep `--interval` at 1 second or higher; the default is 3 seconds.
- Keep repeating bounded waits while the shift is active.

Recommended command:

```bash
bin/baton --db <db> wait --role <role> --timeout 900 --interval 3
```

Reviewer roles waiting for CR review work use:

```bash
bin/baton --db <db> cr wait-review --role <role> --timeout 900 --interval 3
```

Exit handling:

- `0`: A ready job exists. Re-check with `next` before claiming.
- `2`: Timeout. Briefly report the timeout, check shift status, and start another bounded wait if the shift is still active.
- `3`: Stopped. Report stopped once and do not retry until resumed.

Shift handling:

- Start a shift before long-running wait work when the user or SM provides the maximum operating time.
- Use `baton shift status --role <role>` before re-entering wait after a timeout or after finishing work.
- If the shift is expired or stopped, do not re-enter wait.
- If work was already claimed, finish/report it even if the shift expires before the report is submitted.
- After a successful `finish` or CR review action, report success, check shift status, and re-enter the appropriate bounded wait only if the shift is still active.

## Claim Rules

When `wait` returns `0`, immediately re-check the queue:

```bash
bin/baton --db <db> next --role <role>
```

Then claim:

```bash
bin/baton --db <db> claim <job-id> --role <role>
```

The CLI uses identity in this order:

1. `--claimed-by`
2. `BATON_AGENT_ID`
3. `--agent-id-file` or `BATON_AGENT_ID_FILE`
4. role name

The resolved identity should be a stable profile name whenever possible.

If claim fails, do not work on the job. Re-check with `next` or exit.

## Work Rules

- Claim before editing files.
- Work only on the claimed handoff.
- Do not claim work for another role unless the user explicitly authorizes it.
- Do not treat stop/resume as job cancellation.
- If a revision handoff asks you to improve a CR, edit the CR Markdown body and use `cr resubmit`; `finish` alone does not change CR state.

## CR Review Rules

Only roles with CR review permissions can review submitted CRs. Do not approve, reject, or request revision for a CR assigned to another reviewer role.

Review commands:

```bash
bin/baton --db <db> cr request-revision <cr-id> --role <role> --reason "Reason"
bin/baton --db <db> cr approve <cr-id> --role <role> --evidence "Evidence summary"
bin/baton --db <db> cr reject <cr-id> --role <role> --reason "Reason"
```

After approval, create implementation handoffs only when implementation should proceed:

```bash
bin/baton --db <db> cr create-handoff <cr-id> \
  --by-role <role> \
  --role <target-role> \
  --title "Implementation title" \
  --objective "Implementation objective" \
  --exit-criteria "Completion criteria"
```

## Finish Rules

Finish only after completing the task and collecting concrete evidence:

```bash
bin/baton --db <db> finish <job-id> --role <role> --evidence "Evidence summary"
```

If a commit exists for the handoff output, include it:

```bash
bin/baton --db <db> finish <job-id> --role <role> --evidence "Evidence summary" --commit <commit-sha>
```

## Reporting Rules

- Do not emit periodic waiting status messages.
- Report final wait result, claimed job ID, completed work, and evidence.
- Keep reports concise.
