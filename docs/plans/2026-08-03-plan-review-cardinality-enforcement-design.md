# Plan-Review Cardinality Enforcement Design

> **Created:** 2026-08-03
> **Status:** Design Complete
> **Scope mode:** hold
> **Requirements:** `docs/plans/2026-08-03-plan-review-cardinality-enforcement-requirements.md`

## Summary

IronClaude v1.1.2 says that one plan lineage receives exactly one blind plan
review, but the state manager does not enforce that rule. Every
`submit_tier_up_review` call currently inserts a row, a changed plan hash can
receive another blind review, and `start_execution` still tells the agent to
obtain a fresh review after `HAS-ISSUES`. Live database history contains repeated
blind-review rows for the same exact plan hash, confirming that prose alone does
not stop review churn.

This design makes the existing rule a durable state invariant. It does not add a
new review stage, reviewer, approval, or retry mechanism. Each approved design
starts one plan lineage. That lineage may record one blind verdict (`SOLID`,
`HAS-ISSUES`, or `top-tier-self`). A `HAS-ISSUES` lineage may later record the
existing non-blind `advisor-remediated` disposition, but it may never record a
second blind verdict. Only a genuinely new approved design starts another
lineage and earns another review.

## Requirements

1. Reject every second blind plan-review submission in the same lineage before
   inserting a review row or audit row.
2. Changing, regenerating, or reloading a plan must not create a new lineage.
3. A successful transition of a design into `design_ready` starts a new lineage.
   A same-target no-op must not.
4. Both valid design-ready paths (`mark_design_ready` and `consume_design` from
   `brainstorming`) must apply the same lineage rule.
5. `advisor-remediated` remains a non-blind fix-first disposition and is valid
   only after `HAS-ISSUES` in the same lineage.
6. `start_execution` must evaluate only current-lineage review evidence and must
   never instruct an agent to obtain a fresh blind review after `HAS-ISSUES`.
7. Existing review policy modes, task-boundary code review, end-of-execution code
   review, and provider/model selection remain unchanged.
8. Existing databases must migrate without deleting or rewriting historical
   review records. New enforcement must survive process restart.

## Confirmed Root Cause

`submit_tier_up_review` hashes `session.plan_json` and unconditionally calls
`insertTierUpReview`. The `tier_up_reviews` table has neither lineage identity nor
a cardinality constraint. Current lookup helpers select the latest row by session
or by `(session, plan_hash)`, so a later passing row can supersede an earlier
`HAS-ISSUES` row. `create_plan` can reload a revised plan in
`final_plan_prep`, creating a new hash without creating a new design lineage.

The missing concept is therefore not another verdict or workflow stage. It is a
durable identity for the already-defined plan lineage.

## Approaches Considered

### A. Enforce one review per plan hash

**Pros:** smallest query and schema change; exact duplicate submissions become
easy to reject.

**Cons:** fails the stated rule. A formatting change or coherent plan repair
creates a new hash and permits review number two. This preserves the flailing
path under a different key and is rejected.

### B. Enforce one review per terminal session

**Pros:** simple and impossible to bypass with plan edits.

**Cons:** a single Codex/Claude session can run multiple PM loops and can retreat
to a genuinely new approved design. Session-wide cardinality would deadlock all
later valid plans. This conflicts with the accepted v1.1.2 retreat behavior and
is rejected.

### C. Persist a design-derived lineage generation (selected)

**Pros:** directly models the existing contract. Plan edits stay in the same
lineage, while a new approved design creates a new one. It is provider-neutral,
survives restart, and requires no new workflow surface.

**Cons:** requires two additive database columns, migration/bootstrap parity,
and lineage-aware review queries. That storage is necessary because neither
session identity nor plan hash represents a lineage.

This approach best matches the operator's guidance: hard enforcement of an
existing feature, no new review behavior, and no opportunity for repeated blind
reviews to masquerade as convergence.

## Architecture

Add an integer `plan_lineage` generation to `sessions` and
`tier_up_reviews`. New and migrated rows default to generation `0`; future
successful design-ready transitions increment the session generation exactly
once. Every review row records the current session generation at insertion.

Blind verdicts are the existing `SOLID`, `HAS-ISSUES`, and `top-tier-self`
values. Before inserting one, `submit_tier_up_review` queries for an existing
blind verdict for `(terminal_session, plan_lineage)`. If one exists, the tool
returns a stable blocked result that names the original verdict and instructs
the caller to use the existing fix-first path or retreat to a new design. No
review row and no audit row are written.

The handler check supplies a useful error, while a SQLite `BEFORE INSERT`
trigger provides the durable backstop. A trigger is preferred over a new unique
index because historical databases already contain duplicate review rows; the
trigger enforces all future inserts without deleting, renumbering, or falsifying
history.

## Lineage Boundaries

A lineage begins when a design actually transitions into `design_ready`:

- `mark_design_ready`: increment inside the existing atomic transition.
- `consume_design` from `brainstorming`: increment in the same transaction as
  consuming the design and changing the stage.
- Same-target calls: no increment, no audit, and no other mutation.

The following do **not** create a lineage:

- `create_plan`, including a revised plan reload in `final_plan_prep`;
- a changed plan hash, name, task list, or formatting;
- `advisor-remediated`;
- a retreat by itself;
- a task-boundary or final code review.

After a verified `REQUIRES-RETREAT`, the retreat returns to brainstorming. The
subsequent newly approved design increments the generation and its derived plan
earns one blind review. Likewise, a new PM loop after `execution_complete`
receives a new generation when its design becomes ready.

## Review Data Flow

1. Approved design enters `design_ready`; session generation advances from N to
   N+1.
2. Plan is created and may be coherently regenerated without changing N+1.
3. First blind review is submitted with generation N+1.
4. If the verdict is `SOLID` or `top-tier-self`, the existing policy gate may
   advance.
5. If the verdict is `HAS-ISSUES`, all later blind-review submissions for N+1
   are rejected. One non-blind fix advisor dispositions the findings.
6. The coherent revised plan remains generation N+1. `advisor-remediated` binds
   to its current hash and N+1.
7. `start_execution` accepts only current-generation evidence: current-hash
   `SOLID`/`top-tier-self`, or an earlier same-generation `HAS-ISSUES` paired
   with current-hash same-generation `advisor-remediated`.

Review evidence from any older generation cannot satisfy or poison the current
gate, even when a plan hash repeats.

## Error Handling

- **Second blind review:** fail closed with no database or audit mutation. The
  message says the lineage already consumed its one blind review and points to
  advisor remediation or a genuine design retreat.
- **Bare or cross-lineage `advisor-remediated`:** fail closed. An older lineage's
  `HAS-ISSUES` cannot authorize remediation in a newer lineage.
- **No current-lineage review:** retain the normal enforced-policy block before
  execution, but say to obtain the lineage's one review only when none exists.
- **Current-lineage `HAS-ISSUES`:** direct the agent to the existing non-blind
  fix advisor and `advisor-remediated`; never say "fresh review" or "re-review."
- **Database migration:** add missing columns idempotently and preserve all
  historical rows. Historical duplicate rows remain evidence; the trigger
  governs future inserts.
- **Trigger race/backstop:** if a concurrent insert passes the friendly
  pre-check, SQLite aborts the second blind insert. The handler converts that
  constraint failure into the same stable blocked result.

## Testing Strategy

Tests must exercise production handlers and inspect durable state, not merely
assert that documentation contains the word "one."

- First blind review inserts one review row and one audit row with current
  lineage.
- Second same-hash blind review is rejected with unchanged review/audit counts.
- `HAS-ISSUES`, plan content change, and attempted `SOLID` review is rejected in
  the same lineage.
- Formatting-only hash change cannot bypass the cap.
- `top-tier-self` consumes the one blind-review slot.
- `HAS-ISSUES` plus same-lineage `advisor-remediated` still advances after a
  coherent plan change or all-findings-rejected same-hash outcome.
- Bare and cross-lineage `advisor-remediated` remain blocked.
- A real retreat followed by a new approved design increments lineage and permits
  exactly one new blind review.
- A new loop after `execution_complete` receives the same behavior.
- Both design-ready entry paths increment once; same-target transition tests prove
  idempotency.
- Migration succeeds against a database containing historical duplicate rows;
  a post-migration duplicate insert is blocked and remains blocked after reopen.
- `start_execution` messages contain the fix-first path and contain no fresh- or
  repeat-review instruction.
- Focused state-manager tests, hook/bootstrap tests, bundle build, and full
  relevant regression suites pass against the built runtime artifact.

## Files and Compatibility

Expected implementation surface:

- `worker/mcp-servers/state-manager/src/types.ts`
- `worker/mcp-servers/state-manager/src/db.ts`
- `worker/mcp-servers/state-manager/src/tools/write-tools.ts`
- focused state-manager tests and schema/idempotency tests
- `worker/hooks/session-init.sh` and its schema-parity tests
- `worker/mcp-servers/state-manager/dist/index.js` via the existing build
- the narrow executing-plans wording/tests only where stale "fresh review" text
  remains

No Commander routing, Slack behavior, worker provider behavior, code-review
cardinality, directive handling, or unrelated plugin feature is in scope. No
historical rows are deleted. No push is authorized.
