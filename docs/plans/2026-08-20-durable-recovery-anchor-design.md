# Durable Recovery Anchor — Tombstone Reader Hardening (C-1) Design

> **Created:** 2026-08-20
> **Status:** Design Complete
> **Scope mode:** reduction (fix ONLY the reader durable-evidence gap)

## Summary

Follow-up to Loop 3 slice 1 (worktree reaper leak closure, lineage 27 — staged, not
committed). A blind Fable end review found C-1, a never-lose-work hole confirmed at the
source lines: the leak fix corrected the WRITER (`rescueAbandon` now mints
`refs/ironclaude/recovery/<workspace_guid>` and stores that ref NAME in `recovery_ref`)
but not the READER. `tombstoneTerminalAssignment` (workspace-service.ts:665) trusts
`recovery_ref` as durable evidence via `refResolves` (`git rev-parse --verify --quiet`,
:644) in the absent-worktree abandoned case (:677) and via `isAncestor(worktreeHead,
recovery_ref)` in the present case (:673-674). Both `rev-parse` and `isAncestor` resolve
a RAW SHA of any existing object, not only a ref. A legacy row written by the DEPLOYED
pre-change `rescueAbandon` stored `recovery_ref` as a raw SHA with the temporary branch
`ironclaude/<guid>` as the sole live anchor. The new tombstone proof passes on that raw
SHA, then :700-702 deletes the branch, leaving the rescued commit reachable via nothing
(gc-prunable) = WORK LOST. This violates operator directive 1: the durable anchor MUST
be a real ref, and a branch may be deleted ONLY after a durable ref exists.

Fix: an upgrade-on-read durable-anchor gate immediately before branch deletion.

## Requirements (operator-approved)

See `2026-08-20-durable-recovery-anchor-requirements.md` (R1-R4).

## Architecture

Reuse, don't rebuild. Add one private helper and one call site; no schema change, no new
machinery. The gate is a no-op for the normal post-fix ref-name row, so the present-
worktree owner-matched path stays byte-identical.

**`ensureDurableRecoveryAnchor(repository, assignment)`** (new private method,
workspace-service.ts). Returns the durable ref name; upgrades a legacy row in place:
- `recovery_ref` is already a durable ref — `git show-ref --verify --quiet <recovery_ref>`
  succeeds → return `recovery_ref` unchanged (NO re-mint, NO DB write). This is the normal
  case and preserves existing behavior exactly.
- `recovery_ref` is NOT a ref but resolves to a reachable commit object (a legacy raw SHA,
  still anchored by the live branch) — mint `refs/ironclaude/recovery/<workspace_guid>` at
  that commit (`git update-ref <ref> <recovery_ref>`), UPDATE the assignment row's
  `recovery_ref` column to the ref name, return the ref name.
- Neither a ref nor a reachable object → `throw` (preserve + surface; never delete).

**Call site:** inside `tombstoneTerminalAssignment`'s `abandoned` branch, AFTER the
existing resolve/ancestry proof passes and BEFORE `deleteTemporaryBranch` (:700). The raw
SHA is reachable only via the branch, so the ref must be minted while the branch still
exists; `removeWorktree` (worktree dir is not reachability) is unaffected. The `integrated`
branch is untouched: it anchors on `integration.target_ref` (a real ref) with
`integrated_commit` proven an ancestor of it, so branch deletion there is already safe.

## Components

- `worker/mcp-servers/workspace-manager/src/workspace-service.ts` — add
  `ensureDurableRecoveryAnchor` + the one call in the abandoned tombstone path.
- `worker/mcp-servers/workspace-manager/src/__tests__/workspace-service.test.ts` — new
  tests (below). Existing tombstone tests cannot catch C-1 because they mint rows through
  the fixed writer (ref-name `recovery_ref`); the new tests must construct a raw-SHA row.
- `worker/mcp-servers/workspace-manager/dist/{index,cli,hook-intent}.js` — rebuilt bundles.

## Data Flow

Reaper/owner cleanup → `tombstoneTerminalAssignment(abandoned)` → existing durable-evidence
proof (recovery_ref resolves / is ancestor) → `ensureDurableRecoveryAnchor`: ref-name →
no-op; raw SHA → mint `refs/ironclaude/recovery/<guid>` at the commit + update DB → throw
if unanchorable → `removeWorktree` (if present) → `deleteTemporaryBranch` (now safe: a real
recovery ref anchors the commit) → transition `→cleaned`.

## Error Handling

- `recovery_ref` neither a ref nor a reachable object → throw before any deletion; the row
  and its branch are preserved and surfaced. Never-lose-work.
- `update-ref` mint failure → propagates as a throw before branch deletion; preserved.
- A normal ref-name row is a pure no-op — no new failure surface, no DB write.
- Concurrent transition (row changed under us) → the existing `transitionAssignment` guard
  fails closed; reaper records an error and retries next sweep.

## Testing Strategy

Real temp Git repos; assert refs, branch presence, DB `recovery_ref`, and commit
reachability. Cases:
- (a) legacy raw-SHA `recovery_ref`, ABSENT worktree, branch alive → tombstone mints
  `refs/ironclaude/recovery/<guid>` reaching the rescued commit, updates DB to the ref
  name, deletes the branch ONLY after the ref exists, commit still resolvable via the ref
  after branch deletion, row `→cleaned`.
- (b) legacy raw-SHA `recovery_ref`, PRESENT worktree (proof via `isAncestor`) → same
  upgrade + branch safety.
- (c) regression: a normal ref-name `recovery_ref` row → tombstones exactly as before, no
  re-mint, DB `recovery_ref` unchanged (assert the ref value is identical byte-for-byte).
- (d) unanchorable: `recovery_ref` is a bare SHA whose object does NOT exist (branch also
  gone) → tombstone throws, branch (if any) and row preserved.

## Implementation Notes

- Durable-ref detection is `git show-ref --verify --quiet <recovery_ref>` (succeeds only
  for an actual ref path; fails for a raw SHA) — NOT `rev-parse`, which is exactly the
  gotcha that caused C-1.
- Scope=reduction: fix ONLY this reader gap. Non-goals: the 5 non-blocking Observations
  (in-service liveness precondition, remote-host transport, stale git-registration corner,
  unbounded recovery-ref accumulation, `reapReservedAssignment` duplication). No `db.ts`/
  schema change. No bulk migration (upgrade-on-read achieves the same lazily).
- Commits together with lineage 27 under the operator's authority; no push.
