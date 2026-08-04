# Plan-Review Cardinality Enforcement Requirements

> **Created:** 2026-08-03
> **Status:** Operator Approved
> **Authority:** Operator guidance in session `019fc109-7175-7b72-8925-f21d9347e22a`

## Operator Intent

Enforce a hard maximum of one blind plan review per plan lineage. More than one
plan review is flailing, not convergence.

This is a parity and correctness repair for IronClaude v1.1.2. It must make the
existing workflow work with Codex as intended. It must not add product features,
new review stages, or workflow bloat.

## Functional Requirements

1. One plan lineage can record no more than one blind plan review.
2. `SOLID`, `HAS-ISSUES`, and `top-tier-self` each consume that one review slot.
3. A changed or regenerated plan remains in the same lineage and cannot earn a
   second blind review.
4. `HAS-ISSUES` is terminal for the blind review. The existing non-blind fix
   advisor dispositions findings, the plan is regenerated coherently when
   necessary, and `advisor-remediated` records completion without another blind
   review.
5. Only a verified requirements/design retreat followed by a newly approved
   design creates a new lineage that earns one review. A new PM loop after prior
   execution completion likewise begins its plan lineage from its new approved
   design.
6. State-manager enforcement must reject a second blind review before review or
   audit state mutates.
7. Enforcement and gate decisions must survive process restarts and must not
   rely only on prompt text or exact plan hashes.
8. Task-boundary and final code reviews remain unchanged; they are not plan
   reviews and do not consume the plan-review slot.

## Compatibility Requirements

1. Preserve existing tier-up policy modes and provider/model routing.
2. Preserve valid `SOLID`, `top-tier-self`, and
   `HAS-ISSUES` plus `advisor-remediated` execution paths.
3. Prevent older-lineage review evidence from authorizing or blocking a newer
   lineage.
4. Migrate existing databases without deleting or rewriting historical review
   evidence.
5. Keep source schema, hook bootstrap schema, built runtime bundle, and focused
   tests synchronized.

## Out of Scope

- New reviewer types, stages, retries, approvals, or Slack interactions.
- Commander provider routing, worker dispatch, directives, pins, and brain
  chatter.
- Changing code-review frequency or grading.
- Deleting historical reviews or hiding prior flailing evidence.
- Commit or push operations.

## Acceptance Criteria

- A second blind review is rejected for both identical and changed plan hashes
  within one lineage, with no review-row or audit-row mutation.
- A real new design lineage receives exactly one new blind review.
- `advisor-remediated` requires same-lineage `HAS-ISSUES` and remains usable
  after coherent plan regeneration.
- Execution-gate messages never instruct an agent to obtain a fresh or repeat
  blind review after `HAS-ISSUES`.
- Focused migration, transition, state-manager, hook, and runtime-bundle tests
  prove the invariant and remain green after reopening the database.
