# Worktree Reaper — Leak Closure (Loop 3, slice 1) Design

> **Created:** 2026-08-19
> **Status:** Design Complete
> **Scope mode:** reduction (smallest set that closes the ownerless/gone-worktree leak)

## Summary

First slice of Loop 3 (the worktree lifecycle — behavior #3 of the operator's three:
never ask the operator to run git/worktree commands). Closes the managed-worktree
**leak** that once left a game repo with 17 worktrees / 75GB: dead/finished workers'
worktrees are never reaped, and their off-main work is stranded. Two grounded gaps,
both fixed by REUSING the existing rescue/abandon/cleanup machinery — no new
generational/receipt complexity (that was reverted).

1. **Item 1 — gone-worktree tombstone.** `cleanupWorkspace` (workspace-service.ts:644)
   calls `worktreeIsClean`/`worktreeHead`/`removeWorktree` on the worktree path, which
   ERROR when the worktree dir is already gone — so a stale terminal row can never be
   tombstoned.
2. **Item 2 — ownerless reap.** The reaper (main.py:466-479) can only SURFACE (log) an
   ownerless leaked assignment: `cleanupWorkspace`/`abandonWorkspace` require a non-null
   `owner_session_id` match (via `getWorkspaceAssignment`), which a worker that died
   before `bind` never set. So the row (and its worktree) leaks permanently.

Never-lose-work is absolute: preserve any unintegrated work on a recovery ref BEFORE
removing anything.

## Requirements (operator-approved)

- R1: `cleanupWorkspace` tombstones a terminal (`integrated`/`abandoned`) row whose
  worktree dir is **absent** — skip the on-disk `worktreeIsClean`/`worktreeHead`/
  `removeWorktree` steps, still require the recorded durable proof to be reachable in
  the primary checkout (recovery_ref for abandoned; integration evidence for
  integrated), then transition `→cleaned`. A present worktree keeps today's exact
  proofs unchanged.
- R2: A new **reaper-only** service operation reaps an *ownerless* leaked assignment
  WITHOUT an `owner_session_id` match. It preserves first (`rescueAbandon`-style: commit
  unintegrated work → `recovery_ref`, remove the worktree dir → `abandoned`) then
  tombstones (`→cleaned`). It refuses to remove anything it has not first preserved.
- R3: The Commander reaper (main.py ownerless branch, currently `counts["surfaced"]`)
  calls the new op for ownerless/dead-worker candidates instead of only logging.
- R4: The owner-match bypass is scoped strictly to the trusted daemon reaper path for
  rows it has already proven leaked (dead worker + liveness + TTL, via the existing
  `_is_protected`/candidate scan). No session-facing path loses its owner check.
- R5: MUST NOT regress Loop 1 (PM invariants), Loop 2 (commit lane), or the existing
  owner-matched `cleanupWorkspace`/`abandonWorkspace`/`rescueAbandon` behavior. Human
  commits, no push.

## Architecture

Reuse, don't rebuild. The preserve primitive (`rescueAbandon`, workspace-service.ts:599:
rescue-commit → `recovery_ref` → remove worktree dir → `abandoned`) and the tombstone
primitive (`cleanupWorkspace`, :644) already exist and are correct for owner-matched
sessions. This slice removes their two blind spots for the reaper.

**Item 1 (gone worktree):** in `cleanupWorkspace`, branch on whether the worktree dir
exists (`existsSync(worktree_path) || worktreeExists(...)`). Present → today's exact
path (unchanged). Absent → verify the RECORDED durable evidence is reachable
(`abandoned`: `recovery_ref` exists/ancestor; `integrated`: `integrated_commit`
matches the integration record and is an ancestor of the target ref) WITHOUT recomputing
`actualHead` (there is no worktree), skip `removeWorktree`, still
`deleteTemporaryBranch` if the branch remains, and transition `→cleaned`.

**Item 2 (ownerless reap):** a new `reapLeakedAssignment(repositoryPath, workspaceGuid)`
that resolves the row by `workspace_guid` ALONE (no owner binding), then:
- worktree absent → item-1 tombstone path;
- worktree present, unresolved, with unintegrated work → `rescueAbandon`-preserve
  (recovery ref), then tombstone;
- worktree present, already `integrated`/`abandoned` with reachable proof → tombstone.
It NEVER removes a worktree whose work is not first proven preserved (recovery ref or
integration). It validates managed identity (path/branch shape) so it cannot touch a
primary checkout or an unmanaged path.

**Commander reaper (main.py):** the ownerless branch (:466-479) calls the new op through
the workspace-manager CLI/client for candidates already deemed leaked by the existing
scan (`_find_leaked_worktrees`) + `_is_protected` gates; on success `counts["released"]`,
on failure `counts["errors"]`, preserving the existing counters and structured logging.

## Components

- `worker/mcp-servers/workspace-manager/src/workspace-service.ts` — R1 (gone-worktree
  tolerance in `cleanupWorkspace`) + R2 (`reapLeakedAssignment`).
- `worker/mcp-servers/workspace-manager/src/cli.ts` — expose `reapLeakedAssignment` as a
  CLI verb the daemon calls.
- `commander/src/ironclaude/main.py` — R3 (reaper ownerless branch invokes the reap verb).
- Tests: `workspace-service.test.ts` (gone-worktree tombstone; ownerless preserve-then-reap;
  never-lose-work refusal), `cli.test.ts` (new verb), `test_worktree_reaper.py` (ownerless
  candidate is released, not just surfaced), all with real temp Git repos.

## Data Flow

Reaper sweep → `_find_leaked_worktrees` yields candidate + worker → `_is_protected`
filters live/locked/TTL → ownerless (no owner or dead worker) → `reapLeakedAssignment`
by `workspace_guid` → preserve unintegrated work on a recovery ref if present → remove
worktree dir → verify durable proof reachable → transition `→cleaned` → row + disk
reclaimed, work recoverable via the recovery ref.

## Error Handling

- Worktree present + dirty/unintegrated but preserve (rescue-commit) FAILS → leave the row
  and worktree exactly as found; surface, never remove. Never-lose-work.
- Durable proof unreachable after (or without) preserve → refuse tombstone; surface.
- Managed-identity mismatch (path/branch not the managed shape) → refuse; never touch.
- Concurrent transition (row changed under us) → the existing `transitionAssignment`
  guard fails closed; reaper records an error and retries next sweep.
- Owner-matched `cleanupWorkspace`/`abandonWorkspace` behavior is byte-unchanged for the
  present-worktree case.

## Testing Strategy

Real temp Git repos; assert refs, worktree presence, DB `lifecycle_status`, and recovery
refs. Cases: (a) terminal row with a manually-removed worktree → `cleanupWorkspace`
tombstones (item 1); (b) ownerless active row, worktree present with an uncommitted/
unintegrated change → `reapLeakedAssignment` creates a recovery ref reaching the work,
removes the dir, marks `cleaned`; (c) ownerless row whose rescue-commit is forced to fail
→ row and worktree preserved untouched (never-lose-work); (d) reaper integration
(`test_worktree_reaper.py`): an ownerless candidate moves from `surfaced` to `released`;
(e) regression: owner-matched cleanup/abandon/rescue unchanged; a live/protected row is
never reaped.

## Implementation Notes

- Do NOT add owner-match bypass anywhere except the new reaper-only `reapLeakedAssignment`.
- Reuse `rescueAbandon`'s exact preserve mechanics; do not fork a second preserve path.
- No new authority/generation/receipt machinery — reduction scope.
- Ships its own reviewed loop; the remaining Loop 3 items (3 corrective-git, 4
  shared-resource relink, 5 H8 resolution-worker) are separate later slices.
