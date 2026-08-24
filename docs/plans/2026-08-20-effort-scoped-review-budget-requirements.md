# Effort-Scoped Review Budget Requirements (operator-approved)

> **Created:** 2026-08-20
> **Source:** operator directive (2026-08-19, reinforced 2026-08-20) + brainstorm decisions
> C (advisor validates a post-retreat change) and 1 (implicit new-effort boundary).
> Scope: selective. Human commits, no push.

## Approved scope

Bind the one-blind-plan-review budget to the operator-initiated EFFORT, not the auto
`plan_lineage`, so a retreat→re-plan cannot mint a fresh blind review (review-shopping),
while a legitimately-changed plan still proceeds and a genuine new effort still earns its
one review.

- **R1 — effort-scoped lineage increment.** Add a session flag `inherit_review` (0/1, default
  0, via a guarded schema migration). On any transition whose target is `brainstorming` OR
  `debugging` (both design-phase re-entry points), set it from the FROM-stage:
  `idle`/`execution_complete` → 0 (new effort); any RETREAT_SOURCES mid-effort stage
  (design_ready, design_marked_for_use, plan_ready, plan_marked_for_use, final_plan_prep,
  executing, reviewing, plan_interrupted) → 1 (retreat/continuation); `brainstorming`/`debugging`
  or unknown → unchanged. This must be written at BOTH set-sites: (a) `executeWorkflowTransition`
  (MCP transition tools, incl. the `mark_brainstorming` RETREAT_SOURCES hole), and (b)
  `skill-state-bridge.sh` — the PreToolUse hook that raw-`UPDATE`s `workflow_stage` on
  brainstorming/systematic-debugging skill invocation (the dominant live path; it fires before
  the skill, making the later `mark_brainstorming` a no-op) — which must append the same
  FROM-stage-computed `inherit_review` from its `CURRENT_STAGE` read. `mark_design_ready` and
  `consumeDesign` increment `plan_lineage` ONLY when `inherit_review === 0`; when inheriting,
  keep the lineage and reset `inherit_review = 0`.

  > Note (2026-08-21): the design doc `2026-08-20-effort-scoped-review-budget-design.md`
  > describes only the `executeWorkflowTransition` set-site and target=`brainstorming`; the
  > `skill-state-bridge.sh` site and the `debugging` target were added here (operator-approved,
  > from the blind plan review's C2/C2b findings) after the design left brainstorming. A
  > doc-only design-sync is a follow-up; this requirements contract and the plan are authoritative.

- **R2 — no fresh blind review after retreat.** Because a retreat inherits the lineage and a
  lineage permits exactly one blind verdict (existing refusal, write-tools.ts:1471 + DB
  trigger), a fresh blind `submit_tier_up_review(SOLID|HAS-ISSUES)` after retreat MUST be
  refused. Do not weaken that refusal.

- **R3 — changed plan proceeds via advisor-remediated (decision C).** Extend the sanctioned
  non-blind path to a passing base: `submit_tier_up_review` accepts `advisor-remediated` when
  the lineage has ANY canonical blind verdict (passing OR HAS-ISSUES); `start_execution`
  passes a passing canonical verdict at a DIFFERENT hash when `advisor-remediated` exists at
  the current hash. The agent records `advisor-remediated` only after running the advisor.

- **R4 — new effort still earns its one review.** Design entered from a terminal/fresh stage
  increments the lineage → an empty budget → under `enforced` the one blind review is required
  and accepted exactly as today.

- **R5 — preserved invariants / no regression.** HAS-ISSUES→advisor→advisor-remediated at the
  new hash unchanged; hash-mismatch WITHOUT advisor-remediated still blocks (fail-closed);
  `tier_up_review_policy=off` still skips the gate; the `retreat` progress snapshot to
  `plan_history` and design-unconsume are unchanged. Update the executing-plans skill text
  (Step 1.5 / Step 10) to describe inherit-on-retreat + advisor-remediation-for-changes and
  stop promising a fresh review on retreat. Full regression green (state-manager vitest, hook
  suites, commander pytest).

## Non-goals

- No new human-only "new effort" tool (boundary is implicit).
- No change to per-task code review, testing-theatre, or the blast-radius reviewer-tier logic.
- No change to how the blind review itself is dispatched.
