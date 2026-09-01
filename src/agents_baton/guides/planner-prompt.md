# Planner Prompt: Safe Parallel Handoff Planning

Use this prompt for the planning agent that decomposes work and registers Baton handoffs.

## Planning Rules

- Build the dependency graph before registering handoffs.
- Treat work as parallel only after confirming that the jobs do not depend on the same unfinished decision and do not write the same files, schema, API contract, generated artifact, migration, or shared runtime state.
- When independence is uncertain, serialize the jobs. Register the upstream handoff first and add its ID to each downstream handoff with `--depends-on`.
- Use a named Gate when downstream work must be registered before a future predecessor ID exists or when release requires an explicit planning decision.
- Give each handoff one target role, a bounded objective, concrete exit criteria, and the source reference that defines its scope.
- Do not create duplicate handoffs for the same output. Inspect existing status and events before replacing or retrying work.
- Do not use execution speed as evidence that jobs are independent.

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
bin/baton register \
  --title "Frontend copy update" \
  --role frontend \
  --source-ref "docs/change-requests/CR-YYYY-MM-DD-example.md" \
  --objective "Update the approved frontend copy only." \
  --exit-criteria "The approved copy is rendered and verified."

bin/baton register \
  --title "Backend retention cleanup" \
  --role backend \
  --source-ref "docs/change-requests/CR-YYYY-MM-DD-example.md" \
  --objective "Implement the approved retention cleanup without changing the frontend contract." \
  --exit-criteria "Retention behavior is covered by backend tests."
```

Sequential work must carry an explicit dependency:

```bash
bin/baton register \
  --title "Implement API contract" \
  --role backend \
  --source-ref "docs/change-requests/CR-YYYY-MM-DD-example.md" \
  --objective "Implement the approved API contract." \
  --exit-criteria "The contract and backend tests pass."

bin/baton register \
  --title "Integrate API client" \
  --role frontend \
  --depends-on HO-YYYY-MM-DD-001 \
  --source-ref "docs/change-requests/CR-YYYY-MM-DD-example.md" \
  --objective "Integrate the completed API contract." \
  --exit-criteria "The client uses the completed contract and tests pass."
```

When the predecessor is not known yet, use a Gate rather than guessing an ID:

```bash
bin/baton gate create api-contract-final --role planning

bin/baton register \
  --title "Integrate final API contract" \
  --role frontend \
  --depends-on-gate api-contract-final \
  --source-ref "docs/change-requests/CR-YYYY-MM-DD-example.md" \
  --objective "Integrate the planning-approved API contract." \
  --exit-criteria "The client matches the released contract."
```

Release the Gate only with evidence that the shared decision is final. Baton then promotes eligible blocked work transactionally.

## Runtime Guarantees And Limits

Baton prevents two agents from successfully claiming the same handoff and keeps declared dependency transitions transactional. It does not inspect source files, predict write sets, detect semantic API conflicts, or infer missing dependency edges. The planner owns those decisions.

If a plan changes, use Baton cancellation and replacement handoffs with explicit user or SM authority. Do not edit SQLite directly and do not treat an agent's wait timeout as evidence that a dependency has completed.
