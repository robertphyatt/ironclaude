# Effort-Scoped Review Budget — Close the Retreat→Re-Review Backdoor Design

> **Created:** 2026-08-20
> **Status:** Design Complete
> **Scope mode:** selective (hold the backdoor-closure baseline)

## Summary

Once a plan is blind-reviewed, changing it — INCLUDING via retreat→re-plan — must NOT
mint a fresh blind plan review. Today the one-blind-review budget is keyed to
`plan_lineage`, which increments on EVERY brainstorming→design_ready transition
(`mark_design_ready` write-tools.ts:1133, `consumeDesign` :638). A `retreat` returns to
brainstorming (unconsuming the design, clearing the plan) and the next
`mark_design_ready` bumps the lineage → a brand-new lineage with an empty review budget →
under `enforced` policy `start_execution` demands a fresh blind review. An agent that
dislikes a HAS-ISSUES verdict (or hits a mid-execution allowed_files gap) can retreat and
"shop" for a new review. This closes that backdoor by binding the one-review budget to the
operator-initiated EFFORT, not the auto lineage id, while preserving a sanctioned path for
a legitimately-changed plan to proceed.

Operator decisions (2026-08-20 brainstorm): (C) a changed plan after an inheriting retreat
proceeds via `advisor-remediated` (a non-blind one-tier-up advisor validates the change) —
never a fresh blind review; (1) the new-effort boundary is IMPLICIT — a fresh budget is
minted only when design is entered from a terminal/fresh stage (`idle`/`execution_complete`),
and a retreat from an in-progress effort always inherits.

## Architecture

Reuse the existing lineage counter and tier-up machinery; add one session flag and make the
lineage increment conditional. No new human-only tool.

**Grounded mechanism facts (verified in current source):**
- One blind verdict per lineage: `submit_tier_up_review` refuses a 2nd blind verdict for a
  lineage (write-tools.ts:1471-1479) and a DB trigger enforces it (:1490-1497).
- Gate binds (lineage, planHash): `start_execution` (:769-806) — a passing verdict must
  match the current plan hash (:791-796); HAS-ISSUES needs `advisor-remediated` at the
  current hash (:799-803).
- `advisor-remediated` currently requires a prior HAS-ISSUES in the lineage (:1480-1487).
- Lineage increments at `mark_design_ready` (:1133) and `consumeDesign` (:638).
- `retreat` (state-machine.ts:546 `prepareRetreatArtifacts`) unconsumes the design, clears
  the plan, snapshots to `plan_history`; it does NOT touch the lineage.
- The `mark_brainstorming` HOLE: `validateWorkflowTransition` (state-machine.ts:157) accepts
  any RETREAT_SOURCES transition (:127-136) for ANY tool, so `mark_brainstorming` from a
  mid-effort stage (e.g. `executing`) succeeds via the retreat path — an agent could reach
  brainstorming mid-effort without the `retreat` tool and mint a fresh budget. The fix must
  key off the FROM-stage, not the tool.

### Component 1 — effort-scoped lineage increment

New session flag `inherit_review` (INTEGER 0/1, default 0; added by a schema migration).

Set centrally when a transition's target is `brainstorming` (in `executeWorkflowTransition`
or each tool's `applyArtifacts`), based on the FROM-stage:
- from ∈ {`idle`, `execution_complete`} → `inherit_review = 0` (genuine new effort).
- from ∈ RETREAT_SOURCES (`design_ready`, `design_marked_for_use`, `plan_ready`,
  `plan_marked_for_use`, `final_plan_prep`, `executing`, `reviewing`, `plan_interrupted`)
  → `inherit_review = 1` (retreat/continuation — covers BOTH the `retreat` tool and the
  `mark_brainstorming` hole).
- from == `debugging` → leave `inherit_review` UNCHANGED (a brainstorming↔debugging detour
  preserves the prior classification).

`mark_design_ready` and `consumeDesign`: increment `plan_lineage` ONLY when
`inherit_review === 0`. When inheriting (`=== 1`), keep the current lineage and reset
`inherit_review = 0` (one-shot). Default `0` fails TOWARD granting the one review (safe: a
genuine/uncertain new effort still gets its single review; the backdoor is only about NOT
minting EXTRA reviews on retreat).

Result: a retreat keeps the lineage → its one blind review is already consumed → a fresh
blind review is structurally impossible (existing :1471 refusal). Backdoor closed.

### Component 2 — changed plan after retreat → advisor-remediated on a passing base (C)

- `submit_tier_up_review`: allow `advisor-remediated` when the lineage has ANY canonical
  blind verdict (passing SOLID/top-tier-self OR HAS-ISSUES), not only HAS-ISSUES — relax
  the :1480-1487 guard to accept a prior passing verdict too.
- `start_execution` gate: a passing canonical verdict at a DIFFERENT hash passes when
  `advisor-remediated` exists at the current hash (extend :791-796, mirroring the existing
  HAS-ISSUES branch at :799-803).

A legitimately-changed plan (e.g. adding a file to a task's allowed_files) proceeds after a
non-blind advisor validates the change — no fresh blind review. The agent records
`advisor-remediated` only after actually running the advisor (same discipline as the
HAS-ISSUES path).

### Component 3 — executing-plans skill text

Step 1.5 and Step 10 currently say a retreat produces a new lineage that earns its own
review. Update to: a retreat INHERITS the effort's consumed review; a changed plan proceeds
via `advisor-remediated` (non-blind advisor), never a fresh blind review; only a new
operator-initiated effort (design entered from a terminal/fresh state) earns a new blind
review. Keep the `review_summary` inspection and the fail-closed posture.

## Data Flow

New effort: idle/execution_complete → brainstorming (`inherit_review=0`) → design_ready
(lineage++) → plan → create_plan → ONE blind review at the new lineage → execute → complete.

Retreat within effort: executing/plan_ready → (retreat OR mark_brainstorming) → brainstorming
(`inherit_review=1`) → design_ready (lineage UNCHANGED, flag cleared) → re-plan (new hash) →
non-blind advisor → `submit_tier_up_review(advisor-remediated)` at the new hash → gate passes
(inherited passing verdict + advisor-remediated at current hash) → execute. No fresh blind
review anywhere.

## Error Handling

- Hash mismatch WITHOUT advisor-remediated → still blocks (fail-closed), unchanged.
- Attempt to submit a 2nd BLIND verdict in an inherited lineage → refused (:1471), unchanged.
- `advisor-remediated` with NO prior canonical blind verdict in the lineage → still refused
  (a fresh lineage must take its blind review first).
- Uncertain brainstorming entry (default `inherit_review=0`) → grants the one review (safe).

## Testing Strategy

State-manager vitest + hook suites, real DB:
- Retreat inherits: brainstorming→…→review(SOLID)→retreat→brainstorming→mark_design_ready:
  `plan_lineage` UNCHANGED; a subsequent `submit_tier_up_review(SOLID/HAS-ISSUES)` is
  REFUSED (no fresh blind review).
- New effort after completion: execution_complete→brainstorming→mark_design_ready: lineage
  INCREMENTS; a blind review is required and accepted.
- `mark_brainstorming` hole closed: `mark_brainstorming` from `executing`→brainstorming sets
  `inherit_review=1`; the following mark_design_ready does NOT increment.
- Decision C: an inherited lineage with a passing canonical verdict at hash H1 + a changed
  plan at H2 → `advisor-remediated` at H2 accepted → `start_execution` passes; WITHOUT
  advisor-remediated at H2 → blocked.
- HAS-ISSUES path unchanged: HAS-ISSUES→advisor-remediated at the new hash still passes.
- Fail-closed: passing verdict at a different hash with no advisor-remediated → blocked.
- Debugging detour preserves classification: brainstorming(inherit=0)→debugging→brainstorming
  → mark_design_ready increments (still a new effort).

## Implementation Notes

- Session-schema migration adds `inherit_review INTEGER NOT NULL DEFAULT 0` (guarded, like
  prior state-manager migrations).
- Centralize the `inherit_review` set on brainstorming-entry to cover retreat AND
  mark_brainstorming uniformly (do not special-case only the `retreat` tool).
- Do NOT weaken the one-blind-per-lineage refusal or the DB trigger.
- Human commits, no push. Ships its own reviewed loop.
