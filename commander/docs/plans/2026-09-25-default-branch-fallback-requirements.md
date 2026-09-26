# Reaper Target Fallback — Requirements

> **Created:** 2026-09-25
> **Status:** Operator-approved (revised after retreat)
> **Design:** docs/plans/2026-09-25-default-branch-fallback-design.md

## Operator directives (this session)

- An adversarial review of `8c73e3b` against v1.1.12 found that, with `origin/HEAD`
  unset, the reaper judges against a nonexistent `refs/heads/main`.
- The operator chose "Fix now, fold into commit". That meant:
  - fix the fallback;
  - add a test that fails today;
  - fix observations 1 and 2 (the missing reaper CHANGELOG entry and the stale "primary
    branch" wording);
  - squash into the one commit above v1.1.12.
- **Revised premise, re-approved by the operator** after the plan review and the fix
  advisor showed that the original "fix both callers" premise would let merge-then-reap
  fast-forward the operator's live branch:
  - Leave `canonicalDefaultBranchRef` and `mergeOrphanThenReap` unchanged; they fail
    closed.
  - Change only the reaper target, in this order:
    1. `origin/HEAD`'s branch when set;
    2. otherwise the primary checkout's current branch (v1.1.12 behavior);
    3. otherwise, when the primary is detached, the sweep throws and reaps nothing
       (v1.1.12 behavior).
- Out of scope: review observations 3 to 5.

## Acceptance criteria

1. A new `originHeadBranchRef(cwd)` returns `refs/heads/<name>` when `origin/HEAD` points
   at `refs/remotes/origin/<name>`, and `null` otherwise. It never throws.
2. `canonicalDefaultBranchRef` behavior is unchanged: `origin/HEAD`'s branch, else
   `refs/heads/main`. The existing tests for it and the `:2966` merge-then-reap
   fail-closed test pass unmodified.
3. `reapAmbiguousOrphans` targets, in order:
   - `originHeadBranchRef(primary)` when non-null;
   - otherwise `integrationTargetRef(primaryBranch(primary))`;
   - a detached primary with no `origin/HEAD` makes it throw, and nothing is reaped.

   On a `master` repo with no `origin/HEAD`, a clean merged orphan is reaped. The `:2141`
   trunk test passes with its `origin/HEAD` setup removed.
4. The `origin/HEAD`-set behavior from `8c73e3b` is unchanged. The PRESERVE and REAP
   tests still pass.
5. Doc wording and test titles no longer say "primary branch" where the target is the
   reaper target. `CHANGELOG.md` `[Unreleased]` has a reaper entry.
6. The dist bundles are rebuilt. The full workspace-manager vitest suite passes with
   0 failed.
