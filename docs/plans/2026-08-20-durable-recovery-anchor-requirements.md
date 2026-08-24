# Durable Recovery Anchor (C-1) Requirements (operator-approved)

> **Created:** 2026-08-20
> **Source:** blind Fable end review of lineage 27 (C-1) + operator brainstorm decisions
> (upgrade-on-read then reclaim). Scope: reduction. Human commits both loops together, no push.

## Approved scope

Close the C-1 never-lose-work reader hole: `tombstoneTerminalAssignment` must not delete a
private branch on the strength of a raw-SHA `recovery_ref`. Reuse existing primitives; add
no schema change and no new machinery.

- **R1 — durable-anchor gate before branch deletion.** In `tombstoneTerminalAssignment`
  (worker/mcp-servers/workspace-manager/src/workspace-service.ts:665), the `abandoned`
  path MUST, after its existing durable-evidence proof and BEFORE `deleteTemporaryBranch`
  (:700), guarantee that a real git ref anchors the recovery commit. Detection uses
  `git show-ref --verify --quiet <recovery_ref>` (an actual-ref test), NOT `rev-parse`
  (which resolves a raw SHA — the C-1 gotcha). The `integrated` path is unchanged (it
  anchors on `integration.target_ref`, a real ref).

- **R2 — upgrade-on-read for legacy raw-SHA rows.** New private
  `ensureDurableRecoveryAnchor(repository, assignment)`:
  - `recovery_ref` is already a durable ref → return it unchanged; NO re-mint, NO DB write
    (normal post-fix row stays byte-identical).
  - `recovery_ref` is not a ref but resolves to a reachable commit object (legacy raw SHA)
    → mint `refs/ironclaude/recovery/<workspace_guid>` at that commit (`git update-ref`),
    UPDATE the assignment row's `recovery_ref` column to the ref name, return the ref name.
  - neither a ref nor a reachable object → `throw` (preserve + surface; never delete).

- **R3 — never-lose-work preserved.** No branch deletion, worktree removal, or
  `→cleaned`/`→abandoned` transition may occur before a durable ref (or integration) anchors
  the commit. A legacy raw-SHA row's rescued commit MUST remain resolvable via
  `refs/ironclaude/recovery/<guid>` AFTER the branch is deleted. Any unanchorable row is
  preserved untouched.

- **R4 — no regression.** The normal ref-name `recovery_ref` present- and absent-worktree
  tombstone behavior is byte-identical (gate is a no-op). The owner-matched
  `cleanupWorkspace`/`abandonWorkspace` present-worktree path, the `integrated` path, the
  reaper-only ownerless reap, and Loop 1/2 are unchanged. Full regression green
  (workspace-manager vitest, state-manager vitest, hook suites, commander pytest — the
  latter run as explicit-file foreground batches with `-m "not destructive"`; a single
  background run is unreliable).

## Test obligations

New tests in `workspace-service.test.ts` that construct a RAW-SHA `recovery_ref` row
(existing tombstone tests mint via the fixed writer and cannot catch C-1): (a) legacy
raw-SHA absent-worktree row upgraded + branch-safe; (b) legacy raw-SHA present-worktree row
upgraded + branch-safe; (c) regression — normal ref-name row unchanged, no re-mint;
(d) unanchorable raw-SHA (object gone) → throw + preserve.

## Non-goals

The 5 non-blocking Observations from the end review: `reapLeakedAssignment` in-service
liveness precondition; remote-host transport for ownerless leaks; git-registered-but-dir-
missing stale-registration corner; unbounded `refs/ironclaude/recovery/*` accumulation;
`reapReservedAssignment` body duplication. No `db.ts`/schema change. No bulk migration.
Loop 3 items 3/4/5 (corrective-git, shared-resource relink, H8 resolution-worker).
