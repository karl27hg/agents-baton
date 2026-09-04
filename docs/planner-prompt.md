# Planner Prompt: Safe Parallel Handoff Planning

Use this prompt for the planning agent that decomposes work and registers Baton handoffs.

Examples use the pipx-installed `baton` command from `PATH`. From a Baton source checkout, substitute `bin/baton`.

## Planning Rules

- Do not create subagents, child tasks, parallel agent sessions, or delegated background agents while operating under Baton.
- Delegate execution only by registering Baton handoffs for configured project roles. Parallel Baton handoffs are allowed when they satisfy the safety rules below. In explicitly enabled opt-in mode, an agent may notify an existing registered Codex peer task only after the receiving handoff is ready; the message does not replace Baton registration or claim.
- If no eligible role is available, report the blocker to the SM or user instead of bypassing Baton with a subagent.
- If the project enables Git workspace policy, run `workspace check` before registration and do not bypass a `strict` mismatch without an authorized, audited reason.
- For isolated Git worktrees, verify that every agent reports the same `baton project info` database path. Never initialize one Baton database per worktree.
- Keep mutable CR bodies in the shared Baton control root. Use `cr:<cr-id>` for CR-backed handoffs and `<commit-sha>:<path>` for immutable Git design documents.
- Build the dependency graph before registering handoffs.
- Treat work as parallel only after confirming that the jobs do not depend on the same unfinished decision and do not write the same files, schema, API contract, generated artifact, migration, or shared runtime state.
- When independence is uncertain, serialize the jobs. Register the upstream handoff first and add its ID to each downstream handoff with `--depends-on`.
- Use a named Gate when downstream work must be registered before a future predecessor ID exists or when release requires an explicit planning decision.
- Give each handoff one target role, a bounded objective, concrete exit criteria, and the source reference that defines its scope.
- Do not create duplicate handoffs for the same output. Use `baton handoff list` and `baton handoff show` to inspect existing status and payload before replacing or retrying work.
- Do not use execution speed as evidence that jobs are independent.
- A predecessor marked `finished` does not prove that its commit exists in a downstream worktree. Before releasing integration-dependent work, merge or cherry-pick the required commit into its base, or hold the work behind an integration Gate.
- When planning requires a later reconciliation step, register a planning-role handoff that depends on every required worker handoff. If the final predecessor set is not known yet, keep that follow-up behind a named Gate until the plan is complete.
- A planning handoff belongs to the role queue, not to the agent instance that registered it. Write the objective, inputs, decisions, and exit criteria so another planning agent can safely continue it without hidden conversation context.
- A planner or SM with direct design authority does not create and self-review a CR. Record the authoritative contract in the handoff or an immutable design reference, then register implementation work directly. Use a CR when another role proposes a change or independent/user review is required.

## Planner And SM Operating Loop

Before waiting, inspect the applicable shift. Start the default `4h` role shift only when no active, stopped, or expired role/global scope exists. A planner or SM that both reviews CRs and receives handoffs uses the combined watcher:

```bash
baton watch --role planning --timeout 900
```

`watch` checks submitted CRs assigned to the role first, then ready handoffs. Exit `2` is only a bounded-loop timeout: check `shift status` and re-enter `watch` silently while the shift remains active. After each CR decision or completed handoff, return to `watch`. Exit `3`, shift expiry, an unrecoverable error, or explicit user direction ends the loop.

Do not send a final response merely because the current planning action completed while the shift remains active. Baton cannot create a new Codex host turn after the agent ends one. In polling mode, continuous circulation requires repeated bounded `watch` calls. In explicitly enabled Codex peer-notification mode, a planner may end after its own obligations are complete and every ready successor was successfully notified, but it must retain `watch` for CR monitoring, unassigned role work, and notification fallback.

## Opt-In Peer Dispatch Policy

- Every reachable Codex task registers a stable agent profile, runtime thread ID, role, host, and model with `agent session-set`. Runtime metadata never grants permissions.
- After finishing a predecessor, use `notify targets <finished-job>` to promote and inspect only its eligible direct dependents.
- Send the Baton handoff ID to one existing candidate thread. Do not send the full job as an unaudited replacement contract and do not create a new thread.
- Record the actual host result with `notify record --status sent|failed`. After failure, try another candidate or preserve the receiver's `wait`/`watch` fallback.
- Do not notify work that is blocked, cancelled, failed, or waiting on a Gate. The receiver must run `handoff show` and win `claim` before editing.
- One successful delivery record per handoff is the default duplicate-suppression boundary. Additional agents discover the job through Baton rather than repeated broadcast messages.
- Keep a session active while its existing Codex task remains addressable by follow-up messages, even when it is not currently executing. End or replace stale endpoints explicitly.

## Parallel-Safety Decision

A handoff may be open in parallel only when all of the following are true:

1. It can finish without output from the other candidate handoffs.
2. Its write set does not overlap another candidate handoff's write set.
3. It does not change a contract that another candidate consumes.
4. Its test or migration setup does not mutate shared state used by another candidate.
5. Concurrent completion order cannot change the accepted result.

If any answer is false or unknown, express the required order with `--depends-on` or `--depends-on-gate`.

## Registration Examples

Independent work can be registered without dependency edges:

```bash
baton register \
  --title "Frontend copy update" \
  --role frontend \
  --source-ref "cr:CR-YYYY-MM-DD-example" \
  --objective "Update the approved frontend copy only." \
  --exit-criteria "The approved copy is rendered and verified."

baton register \
  --title "Backend retention cleanup" \
  --role backend \
  --source-ref "cr:CR-YYYY-MM-DD-example" \
  --objective "Implement the approved retention cleanup without changing the frontend contract." \
  --exit-criteria "Retention behavior is covered by backend tests."
```

Sequential work must carry an explicit dependency:

```bash
baton register \
  --title "Implement API contract" \
  --role backend \
  --source-ref "cr:CR-YYYY-MM-DD-example" \
  --objective "Implement the approved API contract." \
  --exit-criteria "The contract and backend tests pass."

baton register \
  --title "Integrate API client" \
  --role frontend \
  --depends-on HO-YYYY-MM-DD-001 \
  --source-ref "cr:CR-YYYY-MM-DD-example" \
  --objective "Integrate the completed API contract." \
  --exit-criteria "The client uses the completed contract and tests pass."
```

When the predecessor is not known yet, use a Gate rather than guessing an ID:

```bash
baton gate create api-contract-final --role planning

baton register \
  --title "Integrate final API contract" \
  --role frontend \
  --depends-on-gate api-contract-final \
  --source-ref "cr:CR-YYYY-MM-DD-example" \
  --objective "Integrate the planning-approved API contract." \
  --exit-criteria "The client matches the released contract."
```

Release the Gate only with evidence that the shared decision is final. Baton then promotes eligible blocked work transactionally.

## Failed Upstream Decisions

A failed handoff is not completed work. Its dependency descendants remain blocked while the automatically submitted failure CR is reviewed.

- Approve the failure CR only when retrying the original handoff is the chosen recovery. Then run `baton retry` and let the target role claim the reopened job.
- Reject the failure CR when the attempted approach must not be retried. Then use authorized `baton cancel` to cancel that failed job and its blocked dependency branch.
- Request a CR revision when the failure report lacks enough evidence to decide. Do not create a parallel replacement that leaves the original dependency unresolved.
- Never use `promote-ready`, Gate release, or a replacement handoff to bypass a failed required dependency.

## Approved Design Changes

Do not edit an approved CR body. Classify the impact and preserve unrelated work. Use an approved replacement CR when independent review is required; a planner/SM with direct design authority may instead reference its immutable authoritative design.

- Compatible or additive changes keep existing handoffs and add only the required work.
- Breaking changes use `cr supersede OLD_CR --by NEW_CR --role <admin-role> --reason <reason>`, or `--by-source-ref <immutable-ref>` when the acting planner/SM has direct design authority and no replacement review is required.
- Supersession immediately cancels linked `blocked` and `open` implementation handoffs and their blocked dependency descendants.
- A linked `in_progress` handoff becomes `cancel_requested`. Its claimant pauses before commit or integration while the reason is reviewed. If the work remains valid and its linked CR was not cancelled or superseded, use `cancel-withdraw --reason <review-result>` to preserve the original claim; otherwise direct the claimant to run `cancel-ack` with concrete evidence. Do not register replacement work that depends on an unacknowledged old result.
- Finished handoffs remain immutable audit evidence. Register explicit remediation handoffs under the replacement CR when their output must change.
- A failed linked implementation must complete its failure-CR decision before its parent CR can be cancelled or superseded.
- Use `cancel --force` only when the claimant cannot acknowledge cancellation. Record why cooperative cancellation was impossible.

While a potentially breaking CR is still under review, do not silently reinterpret existing handoffs. Use a Gate for work that was designed to await the decision. If immediate containment is required, explicitly stop the affected role and inspect every affected `open`, `blocked`, `in_progress`, and `cancel_requested` job before resuming it. Review the recorded cancellation reason before using `cancel-withdraw`; it cannot restore work whose implementation CR is already cancelled or superseded.

## Runtime Guarantees And Limits

Baton prevents two agents from successfully claiming the same handoff and keeps declared dependency transitions transactional. It does not inspect source files, predict write sets, detect semantic API conflicts, or infer missing dependency edges. The planner owns those decisions.

Baton also cannot disable subagent or thread-creation tools provided by the agent host. The project `AGENTS.md` or equivalent host policy must enforce the no-subagent rule; this guide defines the required Baton behavior.

If a plan changes, use Baton cancellation and replacement handoffs with explicit user or SM authority. Do not edit SQLite directly and do not treat an agent's wait timeout as evidence that a dependency has completed.
