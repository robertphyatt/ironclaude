# Worktree-Reaper Defects Fix (#1 integration target + #4 dead-owner coverage) Design

> **Created:** 2026-09-25
> **Status:** Design Complete

## Summary

Two evidence-backed correctness defects in the worktree reaper, found during the
2026-09-25 orphan-backlog cleanup (Fable inventory, code-verified):

1. **Integration-target bug.** `reapAmbiguousOrphans` (the row-less orphan reaper)
   judges an orphan branch merged/unmerged (its reap-safety ancestry gate) against
   `integrationTargetRef(primaryBranch(primaryCheckoutPath))` — i.e. whatever branch
   the primary checkout currently has checked out (`git symbolic-ref HEAD`). An
   operator on a feature branch makes the reaper judge against that branch, not the
   repo's canonical default. Hazard: an orphan merged into the feature branch but not
   `main` could be wrongly reaped; or one merged into `main` but not the feature
   branch wrongly preserved. `mergeOrphanThenReap` already uses the canonical
   `canonicalDefaultBranchRef` (`refs/remotes/origin/HEAD`, fallback `main`); the
   reaper must too.
2. **Active-dead-owner coverage gap.** A managed worktree whose worker row says
   `status='running'` but whose tmux session is actually dead is reaped by no path:
   `_find_leaked_worktrees` class 1 requires a *terminal* worker status
   (completed/failed/killed), class 2 requires an *ownerless* active row
   (`owner_session_id` NULL/''), class 3 is `reserved`. A `running`-but-dead worker
   with a non-null (dead) owner falls through all three, so its `active` assignment
   never gets cleaned (this is why the backlog's 5-6 dead-owner worktrees persisted).
   Root cause: nothing reconciles a `running`-but-tmux-dead worker to a terminal
   status, and `_worker_is_live` (main.py:347-356) treats `status=='running'` as live
   *without* checking tmux.

Scope: **reduction** — #1 + #4 only. #4's fix (approach A) also resolves the stale
`running` worker row (defect #3) as a byproduct. Defect #2 (stale repo paths) is out
of scope.

## Architecture

- **#1 (workspace-manager, TypeScript):** a one-line target swap in
  `reapAmbiguousOrphans` — use the existing `canonicalDefaultBranchRef` instead of
  `integrationTargetRef(primaryBranch(...))`. No new code; aligns the reaper's
  ancestry gate with the already-correct `mergeOrphanThenReap` path.
- **#4 (commander daemon, Python) — approach A:** a new pre-reap reconciliation step
  that corrects worker-row liveness (marks `running`-but-tmux-dead workers terminal),
  feeding the **existing** `_reap_leaked_worktrees` class-1 path. No new reaper class,
  no schema change, no new tmux machinery (reuses `tmux.has_session`).

## Components

- **`worker/mcp-servers/workspace-manager/src/workspace-service.ts:990`** — change
  `const target = integrationTargetRef(primaryBranch(repository.primaryCheckoutPath));`
  to `const target = canonicalDefaultBranchRef(repository.primaryCheckoutPath);`.
  Add `canonicalDefaultBranchRef` to the existing `./git` import (already exported at
  `git.ts:209`). Both it and the replaced expression return `refs/heads/<name>`, so the
  downstream `isAncestor(tip, target)` gate is unchanged in shape. This also removes
  the detached-HEAD throw path (`primaryBranch` throws on detached HEAD;
  `canonicalDefaultBranchRef` never throws).
- **`commander/src/ironclaude/main.py` — new `_reconcile_dead_workers(commander_conn, tmux, registry)`:**
  for each commander worker with `status='running'`, resolve its `ssh_host` the way the
  existing `has_session` call sites do (main.py:4811/5118), then check
  `tmux.has_session(tmux_session, ssh_host=...)`; if the session is **definitively
  absent** (returns `False`), mark the worker terminal via
  `update_worker_status(worker_id, 'killed')` (which sets `finished_at`). Call it in
  `_run_maintenance` **before** `_reap_leaked_worktrees`. The existing class-1 leaked
  reaper (finished workers past TTL) then cleans the assignment via
  `reapLeakedAssignment` (rescue to a recovery ref → tombstone). Per-worker
  `try/except`; one failure never aborts the reconciliation.
- **dist:** rebuild the workspace-manager bundle (TS changed) — `cli.js`, `index.js`,
  `hook-intent.js`.
- **Tests:** `commander/tests/` for `_reconcile_dead_workers` + its feed into
  `_find_leaked_worktrees` class 1; `worker/mcp-servers/workspace-manager/src/__tests__/`
  for the reaper judging against the canonical default.

## Data Flow

Hourly `_run_maintenance` → **`_reconcile_dead_workers`** (marks `running`-but-dead
workers `killed`, `finished_at=now`) → `_reap_leaked_worktrees` →
`_find_leaked_worktrees` class 1 sees the newly-killed worker once `finished_at` is
past the TTL → `reapLeakedAssignment` rescues work + tombstones the assignment.
Separately, `reapAmbiguousOrphans` now judges row-less-orphan merged-ness against the
canonical default branch (`refs/remotes/origin/HEAD`), not the primary's current branch.

## Error Handling

- `canonicalDefaultBranchRef` never throws (falls back `refs/heads/main`) — strictly
  more robust than the replaced `primaryBranch` (which threw on detached HEAD).
- **`_reconcile_dead_workers` must NOT kill a worker on a transient error.** Only mark
  terminal when `has_session` returns a definitive `False`. If `has_session` raises or
  the worker's SSH host is unreachable (a remote worker whose host is temporarily
  offline is NOT dead), skip — leave it `running`. Per-worker `try/except`.
- The TTL (default 24h) still gates the actual reap (grace window); reconciliation only
  corrects the status — it does not itself remove anything.
- `reapLeakedAssignment` already preserves work (recovery ref) before tombstoning and
  refuses a worktree on a foreign branch — unchanged.

## Testing Strategy

- **#1 (vitest, `workspace-service.test.ts`):** set the primary checkout on a FEATURE
  branch; seed a row-less orphan whose tip is merged into `origin/main` but NOT the
  feature branch → assert `reapAmbiguousOrphans` REAPS it (judged against the canonical
  default). Falsifiable: against the old `primaryBranch` target it would be preserved
  (tip not an ancestor of the feature branch). Inverse: tip merged into the feature
  branch but not `origin/main` → assert PRESERVED.
- **#4 (pytest):** `_reconcile_dead_workers` marks a `running` worker with
  `has_session=False` as `killed` (+`finished_at`); leaves a `running` worker with
  `has_session=True` running; leaves it running when `has_session` RAISES
  (transient/unreachable) — falsifiable per branch. Plus: a reconciled (killed,
  `finished_at` past TTL) worker's assignment is returned by `_find_leaked_worktrees`
  as a class-1 candidate.
- Full commander pytest + full workspace-manager vitest, both 0 failed.

## Implementation Notes

- No schema change. Reuses `reapLeakedAssignment`, `update_worker_status`,
  `tmux.has_session`, `canonicalDefaultBranchRef`.
- **#3 (stale `running` row) is resolved as a byproduct** of `_reconcile_dead_workers`.
  **#2 (stale repo paths / triple-count) is explicitly OUT of scope** (deferred).
- Leave `_worker_is_live:351` unchanged — the reconcile step makes its
  `status=='running'`→live assumption moot for the reaper path (reduction).
- Deploy: workspace-manager `dist/` → both plugin caches; Commander restart (picks up
  `main.py`). No hook/Brain-rule changes.
- Two-base path note: this doc lives at `commander/docs/plans/`; in the plan JSON,
  `design_file`/`requirements_file` are written WITHOUT the `commander/` prefix
  (resolved from the commander cwd), while `allowed_files` ARE `commander/`-prefixed
  (resolved from the git root).
