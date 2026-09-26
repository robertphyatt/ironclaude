# Worktree-Reaper Defects Fix — Requirements

> **Created:** 2026-09-25
> **Status:** Operator-approved
> **Design:** docs/plans/2026-09-25-reaper-defects-fix-design.md

## Operator directives (this session)

- After cleaning the orphan backlog to a clean slate, fix the reaper DEFECTS that
  the cleanup surfaced (the real "prevent recurrence" work) rather than build the
  rejected Feature-B "adoption".
- Scope (REVISED 2026-09-25 after adversarial review): **#1 only** for this loop.
  **#4 (active-dead-owner) was WITHDRAWN** — a fresh Fable adversarial review showed the
  reconcile-and-mark-killed approach would violate the "seam owns all completion"
  invariant and force-kill operator-held finalization-recovery rows, and that the real
  defect is a *silently-failing terminal `abandon`* (r2's abandon raised every tick for
  ~13 days, swallowed unlogged) — an observability fix planned in a SEPARATE loop, not a
  status flip. #2 (stale repo paths) and #3 also out of scope. Scope mode: reduction.

## Acceptance criteria

1. **#1:** `reapAmbiguousOrphans` judges an orphan branch's merged/unmerged status
   against the repo's **canonical default branch** (`refs/remotes/origin/HEAD`,
   fallback `main`) — via the existing `canonicalDefaultBranchRef` — NOT the primary
   checkout's currently-checked-out branch. A row-less orphan merged into
   `origin/main` but not the primary's feature branch is REAPED; one merged into the
   feature branch but not `origin/main` is PRESERVED.
2. **#4:** the daemon reconciles worker liveness before reaping: a worker with
   `status='running'` whose tmux session is definitively gone is marked terminal
   (`killed`, `finished_at` set) so the existing leaked-worktree reaper (class 1)
   cleans its assignment (rescue → tombstone). This closes the "active row + dead
   non-null owner" gap that let dead worktrees persist.
3. **Safety:** reconciliation must NOT mark a worker terminal on a transient error
   (tmux/SSH failure or unreachable host) or during a spawn race — only on a
   definitive `has_session=False` for a worker past a short spawn grace.
4. No schema change. Reuse `canonicalDefaultBranchRef`, `reapLeakedAssignment`,
   `WorkerRegistry.update_worker_status`, `tmux.has_session`, `_resolve_worker_ssh`.
5. `_worker_is_live` is left unchanged (the reconcile step makes its assumption moot
   for the reaper path).
6. Full commander pytest + full workspace-manager vitest pass (0 failed).

## Non-goals

- #2 (stale repo-path skip / de-dupe of the triple-counted repo).
- Any change to `_worker_is_live`, the reaper safety gates, or the assignments schema.
- Re-architecting the reaper or worker lifecycle.
