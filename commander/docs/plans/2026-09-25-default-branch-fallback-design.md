# Reaper Target Fallback Design

> **Created:** 2026-09-25
> **Status:** Design Complete (revised after retreat; operator re-approved)
> **Scope mode:** reduction
> **Requirements:** docs/plans/2026-09-25-default-branch-fallback-requirements.md

## Summary

Commit `8c73e3b` changed the ancestry target of `reapAmbiguousOrphans`
(`worker/mcp-servers/workspace-manager/src/workspace-service.ts:990`):
- **Before (v1.1.12):** `integrationTargetRef(primaryBranch(...))`.
- **After:** `canonicalDefaultBranchRef(...)`.

`canonicalDefaultBranchRef` (`git.ts:202-216`) returns a hardcoded `refs/heads/main`
whenever `refs/remotes/origin/HEAD` is unset. This hurts a `master` or `trunk` repo that
has no `origin/HEAD` (`origin/HEAD` is set only by `git clone` or `git remote set-head`):
- The reaper judges orphans against a ref that does not exist.
- `isAncestor` maps git's exit 128 to `false`, so fully merged orphans are classified
  `genuinely-unmerged` and are never reaped.

The fix changes **only the reaper's target**. `canonicalDefaultBranchRef` and its other
caller, `mergeOrphanThenReap` (`workspace-service.ts:1357`), stay unchanged. The two
paths differ in what they can damage:
- **`mergeOrphanThenReap` advances refs.** It runs a CAS `update-ref` on the target and,
  when the primary is checked out on the target, runs `read-tree -m -u` on the
  operator's checkout.
  - Falling back to the primary's live branch there could fast-forward a branch the
    operator is sitting on.
  - Its fail-closed behavior when the target is unresolvable is intentional. The test at
    `workspace-service.test.ts:2966` checks it.
- **The reaper never advances a ref.** It removes only orphans it judges merged into
  the target, and preserves the rest.
  - When `origin/HEAD` is unset, judging against the primary checkout's branch is
    exactly v1.1.12's behavior.

## Architecture

- A new helper, `originHeadBranchRef(cwd): string | null`, in `git.ts`. It is the
  `origin/HEAD` half of today's `canonicalDefaultBranchRef`:
  - it returns `refs/heads/<name>` when `refs/remotes/origin/HEAD` symbolically resolves
    to `refs/remotes/origin/<name>`;
  - otherwise it returns `null`;
  - it never throws.
- `canonicalDefaultBranchRef(cwd)` becomes `originHeadBranchRef(cwd) ?? 'refs/heads/main'`.
  This is a refactor with no behavior change.
- The reaper target at `workspace-service.ts:990` becomes
  `originHeadBranchRef(p) ?? integrationTargetRef(primaryBranch(p))`, where `p` is
  `repository.primaryCheckoutPath`. The resulting fallback order:
  1. `origin/HEAD`'s branch (the `8c73e3b` behavior);
  2. the primary checkout's current branch (v1.1.12);
  3. if the primary is detached, `primaryBranch` throws, so `reapAmbiguousOrphans` throws
     and nothing is reaped.

  Step 3 is v1.1.12's fail-safe. The daemon's per-repo `except` in `_reap_orphans`
  counts the throw as a `repo_failures` entry (`commander/src/ironclaude/main.py:571`)
  and moves on to the next repo.

## Components

- `git.ts`: add `originHeadBranchRef`, and rewrite `canonicalDefaultBranchRef` on top of
  it. The doc comment keeps its "falls back to refs/heads/main" contract.
- `workspace-service.ts`:
  - import `originHeadBranchRef`;
  - change the reaper target at :990;
  - reword the doc comment at :981-982 from "ancestor of the primary branch" to "ancestor
    of the reaper target (origin/HEAD's default branch, else the primary checkout's
    branch)".
- The docs for `mergeOrphanThenReap` (:1335-1340) and the `integrationTarget` JSDoc
  (:173-181) are unchanged. They stay accurate.
- Test titles at `workspace-service.test.ts:1666`, `:1692` and `:1748`: "primary branch"
  becomes "reaper target".
- `CHANGELOG.md` `[Unreleased]`: a reaper entry that describes the target and its
  fallback, and says merge-then-reap is unchanged.
- A rebuild of the dist bundles.

## Testing Strategy (vitest)

- **The trunk squash-merge test at `workspace-service.test.ts:2141`:** remove the
  `origin/HEAD` setup that `8c73e3b` added (:2150-2152). The test then covers a `trunk`
  repo with no `origin/HEAD`. It fails today and passes after the fix.
- **New reaper test:** a repo with `init --initial-branch=master`, no `origin/HEAD`, and
  a clean, merged, row-less orphan, run with `ttlHours: 0`, should end up `reaped`. It
  fails today, because the orphan is `preservedUnmerged` against a nonexistent `main`.
- **New reaper test:** a detached primary with no `origin/HEAD` should make
  `reapAmbiguousOrphans` throw with a detached-HEAD message and remove nothing. It fails
  today, because the current code judges silently against `main`.
- **New `originHeadBranchRef` unit tests:** `null` when `origin/HEAD` is unset;
  `refs/heads/trunk` when `origin/HEAD` points at `refs/remotes/origin/trunk`.
- **Unchanged:**
  - the existing `canonicalDefaultBranchRef` tests (`main` fallback, `trunk` via
    `origin/HEAD`);
  - the `:2966` fail-closed test for the path that moves branches;
  - the PRESERVE and REAP tests from `8c73e3b` (feature branch versus `origin/HEAD`).

## Implementation Notes

- Out of scope: review observations 3 to 5.
- After the loop, squash into the single commit above v1.1.12.
- Deploy: refresh the plugin-cache dist.
