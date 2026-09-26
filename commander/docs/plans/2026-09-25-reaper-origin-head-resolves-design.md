# Reaper: Adopt origin/HEAD Only When Its Local Branch Exists — Design

> **Created:** 2026-09-25
> **Status:** Design Complete
> **Scope mode:** reduction
> **Requirements:** docs/plans/2026-09-25-reaper-origin-head-resolves-requirements.md

## Summary

`reapAmbiguousOrphans` (`worker/mcp-servers/workspace-manager/src/workspace-service.ts:997-998`)
uses `originHeadBranchRef(p)` whenever it is non-null.

`originHeadBranchRef` (`git.ts:207-214`) maps `refs/remotes/origin/HEAD` →
`refs/remotes/origin/<name>` to `refs/heads/<name>`. It does not check that the local
branch exists. `git symbolic-ref --quiet` exits 0 even for a dangling symref, so the
helper can hand back a ref that isn't there.

In two ordinary setups, `<name>` has no local branch:
- a clone made with `git clone -b develop`, where `origin/HEAD` → `origin/main` but there
  is no local `main`;
- a stale `origin/HEAD` left behind after the remote's default branch was renamed.

In both, `isAncestor` returns false for every orphan (git exits 128). Merged orphans are
then preserved as `genuinely-unmerged` with a misleading reason, and merge-then-reap
fails on the same missing ref, so the orphan comes back every sweep. v1.1.12 reaped these
orphans.

## Architecture

Change only the reaper:

```ts
const originHead = originHeadBranchRef(repository.primaryCheckoutPath);
const target = originHead && this.refResolves(repository.primaryCheckoutPath, originHead)
  ? originHead
  : integrationTargetRef(primaryBranch(repository.primaryCheckoutPath));
```

- `this.refResolves` (`workspace-service.ts:749`) already exists. It runs
  `rev-parse --verify --quiet` and returns a boolean.
- The fallback is v1.1.12's target. A detached primary still throws, so the sweep fails
  safe.
- `mergeOrphanThenReap` and `canonicalDefaultBranchRef` do not change. merge-then-reap
  still fails closed when its target does not resolve.

## Components

- `workspace-service.ts`:
  - the target selection at :997-998;
  - its explanatory comment at :991-996, updated to mention the resolves check;
  - the reaper doc comment at :981-984, re-wrapped to about 78 columns.
- `git.ts` doc comments:
  - `originHeadBranchRef`: null when `origin/HEAD` is unset or does not point under
    `refs/remotes/origin/`. The returned local ref may not exist (for example after a
    `clone -b` or a renamed remote default), so callers must verify it.
  - `canonicalDefaultBranchRef`: the same clarification. The fallback to `main` applies
    when `origin/HEAD` is unset or malformed. The returned ref may not exist locally, and
    callers that advance it fail closed.
- `CHANGELOG.md` `[Unreleased]` reaper bullet: say that `origin/HEAD`'s branch is used
  only when it exists locally.
- A rebuild of the dist bundles.

## Testing Strategy (vitest, `workspace-service.test.ts`, inside `describe('reapAmbiguousOrphans')`)

- **`clone -b`:**
  - A source repo on `main` gets a `develop` branch with one extra commit.
  - `git clone -q -b develop <src> <dst>` gives a clone where `origin/HEAD` →
    `origin/main` and there is no local `main`.
  - An orphan worktree at the clone's HEAD (`develop`), run with `ttlHours: 0`, is
    `reaped`, and `preservedUnmerged` is empty.
  - Fails today: the target is `refs/heads/main`, which is missing.
- **Renamed default:**
  - A `repository()` (on `main`) with
    `symbolic-ref refs/remotes/origin/HEAD refs/remotes/origin/master` and no `master`.
  - An orphan at `main` is `reaped`.
  - Fails today: the target is `refs/heads/master`, which is missing.
- **Unchanged:** the existing tests with `origin/HEAD` set and a local branch present
  (PRESERVE and REAP against a feature branch), the `master`/no-`origin/HEAD` test, and
  the detached fail-safe test.

## Implementation Notes

- Out of scope: review observations 1, 2 and 4.
- After the loop, amend into `93bd9dc`, the single commit above v1.1.12.
- Deploy: refresh the plugin-cache dist.
