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
- Keep `role` for authorization and use optional `--workstream` for specialization within a broad role. Prefer stable domain names such as `api-contract`, `ui-regression`, or `data-migration`; do not encode an agent name in the workstream.
- At a validation fan-in, register independent evidence-producing workstreams in parallel, then one final integration handoff that depends on every required result. The final merge, acceptance decision, and Gate release remain serialized.
- Do not create duplicate handoffs for the same output. Use `baton handoff list` and `baton handoff show` to inspect existing status and payload before replacing or retrying work.
- Do not use execution speed as evidence that jobs are independent.
- A predecessor marked `finished` does not prove that its commit exists in a downstream worktree or that its structured completion outcome passed. Handoff dependencies are after-completion edges. Before releasing success-dependent or integration-dependent work, inspect the outcome and effective commit, merge or cherry-pick the required commit into its base, and use an integration Gate for the acceptance decision.
- When planning requires a later reconciliation step, register a planning-role handoff that depends on every required worker handoff. If the final predecessor set is not known yet, keep that follow-up behind a named Gate until the plan is complete.
- A planning handoff belongs to the role queue, not to the agent instance that registered it. Write the objective, inputs, decisions, and exit criteria so another planning agent can safely continue it without hidden conversation context.
- A planner or SM with direct design authority does not create and self-review a CR. Record the authoritative contract in the handoff or an immutable design reference, then register implementation work directly. Use a CR when another role proposes a change or independent/user review is required.

## Planner And SM Operating Loop

Before waiting, inspect the applicable shift. Start the default `4h` role shift only when no active, stopped, or expired role/global scope exists. A planner or SM that both reviews CRs and receives handoffs uses the combined watcher:

```bash
baton watch --role planning --timeout 900
```

`watch` checks submitted CRs assigned to the role first, then ready handoffs. Use `cr list --status submitted --reviewer-role <role>` for an explicit queue audit. Exit `2` is only a bounded-loop timeout: check `shift status` and re-enter `watch` silently while the shift remains active. Use `watch --explain` when a timeout needs identity, workstream, or ownership diagnosis. After each CR decision or completed handoff, return to `watch` unless the push-first conditions below are satisfied. Exit `3`, shift expiry, an unrecoverable error, or explicit user direction ends the loop.

In polling mode, do not send a final response merely because the current planning action completed while the shift remains active; continuous circulation requires repeated bounded `watch` calls. In explicitly enabled Codex peer-notification mode, a planner may instead become addressable idle and end its current host turn when it owns no active handoff or claimed review, every expected return path is an explicit planning handoff, and every producer can notify the planner's active session. Addressable idle is not a Baton workflow status and has no waiter lease. On the next host message, re-read Baton with `watch`, `next`, or the referenced `handoff show` before claiming anything.

Retain `watch` for CR monitoring that has no notification path, unassigned planning-role work, stale or unavailable endpoints, failed delivery, and non-Codex hosts. A `sent` audit value means only host acceptance; inspect `notify status <job>` when claim acknowledgement is delayed. Never assume that host acceptance means the planner observed or claimed the handoff.

## Opt-In Peer Dispatch Policy

- Use `handoff successors <finished-job>` for transport-neutral workflow inspection. It reports required roles and existing claimants; an `open` and `unassigned` successor is not assigned to the inspecting agent.
- Every reachable Codex task registers a stable agent profile, runtime thread ID, role, host, and model with `agent session-set`. Runtime metadata never grants permissions.
- `finish` immediately opens eligible direct dependents. After finishing a predecessor, use `notify targets <finished-job>` only to inspect opt-in Codex delivery candidates.
- For an open handoff with no real scheduling predecessor, use `notify candidates <ready-job>`; never add a false dependency only to create a messaging route.
- Send the Baton handoff ID to one existing candidate thread. Do not send the full job as an unaudited replacement contract and do not create a new thread.
- Record the actual host result with `notify record --status sent|failed`. Stored `sent` is displayed as `host_accepted` and does not prove observation or claim. Use `notify status <job> --stale-after <duration>` for derived liveness diagnosis. After failure, try another candidate or preserve the receiver's `wait`/`watch` fallback.
- A stale host-accepted result permits at most one same-recipient recovery while the handoff is open/unclaimed, the original session is active and idle, and the target shift is active. Send that follow-up first, then record it with `notify retry --notification <id> --reason stale_unclaimed --status sent|failed`. Failed recovery consumes the allowance; do not broadcast again.
- Audit recent delivery activity with `notify list --limit <count>`; newest-first is the default. Use exclusive ID cursors and `--recovery-only` when narrowing the audit, and explicit `--order oldest` only for chronological history. Do not replace Baton filtering with ad hoc database writes.
- Treat `outside_shift` as a delivery prohibition, not as a missing assignment. Leave the ready handoff `open`; the target role will discover it after its project-local shift resumes.
- Do not notify work that is blocked, cancelled, failed, or waiting on a Gate. The receiver must run `handoff show` and win `claim` before editing.
- One successful delivery record per handoff attempt is the duplicate-suppression boundary. Reviewed retry creates a new attempt; additional agents discover the same attempt through Baton rather than repeated broadcast messages.
- Keep a session active while its existing Codex task remains addressable by follow-up messages, even when it is not currently executing. End or replace stale endpoints explicitly.
- Do not require peer delivery from non-Codex hosts or other models. Their interoperable baseline is Baton state plus `next`, `wait`, `watch`, and `claim`.
- Candidate selection respects workstream registration and excludes agents that already own an active handoff or claimed CR review. Delivery still does not reserve capacity, so the receiver must claim before editing. At convergence points, register one fan-in handoff that depends on every required branch, optionally behind a Gate, so readiness and notification occur once after all inputs finish.

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
- A standalone remediation handoff must cite the failed job in `--source-ref`; do not make it depend on that failed job. Use a scheduling dependency only when the remediation must wait for the original job to be retried and finished.
- `retry` increments the original handoff attempt. If peer notification is enabled, record the corrected-baseline delivery against that new attempt.
- Never use `promote-ready`, Gate release, or a replacement handoff to bypass a failed required dependency.

## Completed Validation Outcomes

A validation or analysis handoff may complete with `completion_outcome=fail|conditional|inconclusive` and `completion_blocking=1`. This differs from lifecycle `failed`: the assigned check completed, so ordinary handoff dependencies are satisfied.

- Inspect blocking completed results with `handoff list --status finished --blocking yes`, `status`, or `baton-report summary`.
- Treat the compatibility `outcome.blocking` count as a historical total. Use the RC7 open, implemented, terminal-unimplemented, and no-CR context counts plus `handoff show` before deciding whether action remains. Never infer risk acceptance from a rejected, cancelled, or superseded outcome CR.
- Put implementation that requires a passing result behind a named Gate. Release or cancel it only after reviewing `handoff show`, the effective commit evidence, and any `outcome_cr_id`.
- Do not change the finished row to hide a verdict. Register remediation or a new validation handoff with an explicit source reference.
- If commit evidence is wrong, append a reviewed correction with `handoff evidence-correct --reason ...`. `planning` and `sm` receive `handoff.evidence_correct` by default; the original claimant may also correct its own finished handoff under the target role.
- Treat an unresolved or `legacy_unchecked` commit as evidence requiring independent verification before integration. The unresolved override is for deliberate external or not-yet-fetched references, not ordinary typos.

## Approved Design Changes

Do not edit an approved CR body. Classify the impact and preserve unrelated work. Use an approved replacement CR when independent review is required; a planner/SM with direct design authority may instead reference its immutable authoritative design.

- Compatible or additive changes keep existing handoffs and add only the required work.
- Breaking changes use `cr supersede OLD_CR --by NEW_CR --role <admin-role> --reason <reason>`, or `--by-source-ref <immutable-ref>` when the acting planner/SM has direct design authority and no replacement review is required.
- Supersession immediately cancels linked `blocked` and `open` implementation handoffs and their blocked dependency descendants.
- A linked `in_progress` handoff becomes `cancel_requested`. Its claimant pauses before commit or integration while the reason is reviewed. If the work remains valid and its linked CR was not cancelled or superseded, use `cancel-withdraw --reason <review-result>` to preserve the original claim; otherwise direct the claimant to run `cancel-ack` with concrete evidence. Do not register replacement work that depends on an unacknowledged old result.
- Finished handoffs remain immutable audit evidence. Register explicit remediation handoffs under the replacement CR when their output must change.
- Use `cr create-handoff` for normal implementation assignment. If a general handoff with the exact `cr:<CR-ID>` source reference already finished before official linkage, the assigned reviewer may inspect and adopt it with `cr link-handoff`. Adoption requires a non-blocking result and verified effective commit evidence. The default candidate is only a mechanical hint for an approved CR with no official implementation; audit other exact-source records with `--include-related-handoffs`. Never create a synthetic no-change handoff merely to repair linkage, and never infer or backfill a link from titles or similar content.
- When one implementation route of the same approved CR was cancelled and replaced, record `cr supersede-handoff <cr> <cancelled-job> --replacement <new-job> --role <reviewer> --reason <reason>`. Do not mark the CR implemented until every replacement chain reaches a finished, non-blocking handoff.
- A failed linked implementation must complete its failure-CR decision before its parent CR can be cancelled or superseded.
- Use `cancel --force` only when the claimant cannot acknowledge cancellation. Record why cooperative cancellation was impossible.

While a potentially breaking CR is still under review, do not silently reinterpret existing handoffs. Use a Gate for work that was designed to await the decision. If immediate containment is required, explicitly stop the affected role and inspect every affected `open`, `blocked`, `in_progress`, and `cancel_requested` job before resuming it. Review the recorded cancellation reason before using `cancel-withdraw`; it cannot restore work whose implementation CR is already cancelled or superseded.

## Runtime Guarantees And Limits

Baton prevents two agents from successfully claiming the same handoff and keeps declared dependency transitions transactional. It does not inspect source files, predict write sets, detect semantic API conflicts, or infer missing dependency edges. The planner owns those decisions.

Baton permits one active claimed handoff or submitted CR review per concrete agent identity. This limits message pile-up, but it does not increase the throughput of a genuinely serial integration decision. Reduce that bottleneck by splitting independent evidence collection into workstreams, keeping integration ownership explicit, and avoiding parallel writes to the same integration branch or shared environment.

Baton also cannot disable subagent or thread-creation tools provided by the agent host. The project `AGENTS.md` or equivalent host policy must enforce the no-subagent rule; this guide defines the required Baton behavior.

If a plan changes, use Baton cancellation and replacement handoffs with explicit user or SM authority. Do not edit SQLite directly and do not treat an agent's wait timeout as evidence that a dependency has completed.
