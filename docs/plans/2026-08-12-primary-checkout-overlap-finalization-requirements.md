# Primary Checkout Overlap-Based Finalization Requirements

> **Created:** 2026-08-12
> **Status:** Operator Approved
> **Design:** `docs/plans/2026-08-12-primary-checkout-overlap-finalization-design.md`

## Purpose

Permit safe worker integration while another session owns the primary checkout.
Primary ownership must remain exclusive for direct checkout use, but must not
stand in for evidence that integration would overwrite local work.

## Functional Requirements

1. IronClaude must retain exclusive ownership when a session requests direct
   primary-checkout access.
2. A second session must not acquire direct primary-checkout access while a live
   owner holds it.
3. Ownership lifecycle operations may reap a stale owner before granting direct
   access.
4. Worker finalization must not fail solely because a live primary-checkout owner
   exists.
5. When the target branch is not checked out in the primary checkout,
   finalization must use the existing ref-only CAS path without modifying primary
   checkout files or index state.
6. When the target branch is checked out in the primary checkout, finalization
   must compare the proposed integration's changed paths with all staged,
   unstaged, and untracked primary-checkout paths before target-ref CAS.
7. Any path overlap must refuse integration before CAS, report the exact
   overlapping paths, leave the target ref unchanged, and preserve the managed
   worktree for retry.
8. With no path overlap, finalization must proceed through the established CAS
   and carry-forward flow while preserving all pre-existing primary-checkout
   bytes and index state.
9. Reconciliation must use the same ref-versus-working-tree classification and
   overlap rules as normal finalization.
10. Push-only operations must not be fenced by primary-checkout ownership.
11. Status and probe operations must report relevant state without fencing or
    mutating ownership.
12. Managed-worktree release or recycling after integration must not require the
    primary checkout to be unowned.
13. Direct sessions and Commander-managed workers must use the same finalization
    safety behavior.
14. `WorkspaceService.cleanupWorkspace()` and Commander's leaked-worktree
    reaper must not protect a terminal managed worktree solely because an
    unrelated session owns the repository's primary checkout.

## Safety Requirements

1. Preserve the existing integration lock, expected-target comparison, target-ref
   CAS, target-moved handling, carry-forward behavior, and post-CAS recovery.
2. No failure path may discard worker output or overwrite staged, unstaged, or
   untracked primary-checkout work.
3. A target-ref race must preserve newer history and produce a concrete
   expected-versus-actual failure.
4. A push failure must preserve successful local integration and report the
   remote failure without rolling back the target ref.
5. A cleanup failure must distinguish integration success from cleanup failure
   and retain assignment state needed for retry.
6. Unexpected repository state must fail closed with concrete repository, ref,
   commit, ownership, and path evidence where applicable.

## Verification Requirements

Tests must use temporary real Git repositories and verify:

1. Live owner plus target checked out elsewhere permits ref-only finalization and
   leaves primary bytes unchanged.
2. Live owner plus non-overlapping staged work permits finalization and preserves
   bytes and index state.
3. Live owner plus non-overlapping unstaged work permits finalization and
   preserves bytes and index state.
4. Live owner plus non-overlapping untracked work permits finalization and
   preserves bytes and index state.
5. Overlapping staged, unstaged, and untracked cases each refuse before CAS and
   leave the target ref unchanged.
6. A second direct-primary request remains fenced by live ownership.
7. Stale-owner cleanup remains effective for ownership lifecycle operations.
8. Status probes neither fence nor mutate ownership.
9. Push-only proceeds independently of primary ownership.
10. Managed-worktree cleanup does not require an unowned primary checkout.
11. Target movement retains CAS protection.
12. Post-CAS failure retains recovery guarantees.
13. Direct-session and Commander paths share the behavior.
14. Focused workspace-manager tests and the full repository test suite pass.
15. Direct `cleanupWorkspace()` removes a proven terminal worktree under an
    unrelated live primary owner while preserving primary-checkout bytes and
    the unrelated owner row.
16. Commander reaps an otherwise eligible terminal worker under an unrelated
    live primary owner while preserving worker/tmux liveness, recent-activity,
    operator-assignment, integration-lock, and fail-safe exception guards.

The existing test that requires every live owner to fence finalization must be
replaced because it asserts the behavior being repaired.

## Scope Boundaries

This effort must not add database schema, path reservations, distributed locks,
cross-session `allowed_files` coordination, automatic conflict resolution,
commit or push authority changes, or unrelated finalization or reaper
refactoring.
