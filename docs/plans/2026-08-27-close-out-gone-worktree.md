# Gone-Worktree Close-Out Completion (R3), Unified with the Reaper — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Complete a crash-mid-teardown `/close-out` (integrated row, worktree already gone) DB-only
without a raw error, and unify the behavior with the reaper so `tombstoneTerminalAssignment` carries a
push-pending obligation into `preserved_work` instead of refusing.

**Requirements:** docs/plans/2026-08-26-m6-close-out-requirements.md (R3, R6, R1b, D4)

**Design:** docs/plans/2026-08-27-close-out-gone-worktree-design.md

**Architecture:** (1) `tombstoneTerminalAssignment` (workspace-service.ts) replaces the push-pending
refusal with a carry — `pushPendingSummary(disposition)` → `insertPreservedWork` wrapped transactionally
with the `integrated→cleaned` transition; this changes both `cleanupWorkspace` (owner-bound) and
`reapLeakedAssignment` (reaper). (2) The `closeOutWorktree` handler (index.ts) detects an integrated row
whose worktree is absent (same `fs.existsSync && worktreeExists` present-check `cleanupWorkspace` uses)
and routes to `service.cleanupWorkspace(…, ownerSessionId: identity.sessionId)`, mapping the result to
`closed-out` + `pendingPush`. No push anywhere; the live-worktree `/close-out` path and the C1/C2/I2
remediation stay byte-untouched; the shared `resolveEffectiveCheckout` is NOT modified.

**Tech Stack:** TypeScript, vitest, better-sqlite3, git.

**Execution invariants:** absolute paths; `docs/` gitignored (`git add -f`); vitest
`--testTimeout=30000`; each task's full-suite run is its falsifier; distinctive `-t` test names so a RED
filter does not sweep existing green tests; baseline 288 green (post-M6-remediation). Bash cwd is
`commander/`; use `git -C /Users/roberthyatt/Code/ironclaude` for staging.

---

## Task 1: `tombstoneTerminalAssignment` carries the push-pending obligation

**Files:**
- Modify: `worker/mcp-servers/workspace-manager/src/workspace-service.ts` (imports; `:730-743`)
- Test: `worker/mcp-servers/workspace-manager/src/__tests__/workspace-service.test.ts`

The push-pending refusal at `tombstoneTerminalAssignment` (`:730-732`,
`throw 'Refusing to tombstone a worktree with a push-pending obligation…'`) becomes a carry, so a
push-pending integrated row (present or gone) is reclaimed and its owed push preserved in
`preserved_work`. `pushPendingSummary` is the single source of truth for BOTH detection and payload (it
already excludes `push-succeeded`, matching closeOutRelease obs-2). Affects both callers of tombstone
(`cleanupWorkspace`, `reapLeakedAssignment`).

**Step 1 — RED (rewrite the refusal test + add a reaper-carry test):** In `workspace-service.test.ts`:
- Rewrite `it('refuses to tombstone an integrated worktree with a push-pending obligation'…)` (`:762`) →
  `it('carries a push-pending obligation into preserved_work when tombstoning an integrated worktree'…)`:
  same seed (integrated push-pending row, PRESENT clean worktree, `recordIntegration`, `merge --ff-only`),
  but assert `cleanupWorkspace(…)` does NOT throw; the row is `cleaned`; the worktree is removed
  (`existsSync(worktree_path) === false`); the branch is gone; and
  `SELECT … FROM preserved_work WHERE workspace_guid=? AND kind='pending-push' AND resolved_at IS NULL`
  returns a row whose payload JSON has `candidateCommit === integratedCommit` and
  `destinationRef === 'refs/heads/main'`.
- Add `it('reapLeakedAssignment carries a push-pending dead-worker obligation (integrated + gone worktree)'…)`:
  seed an integrated push-pending row (mirror the `:762` seed), then remove the worktree with
  `git(root, 'worktree', 'remove', '--force', worktree_path)`, clear the owner
  (`UPDATE assignments SET owner_session_id = NULL WHERE workspace_guid = ?`) so it is a LEAKED row, then
  `manager.reapLeakedAssignment({ repositoryPath: root, workspaceGuid })` → assert `cleaned` + a
  `preserved_work` `pending-push` row with the disposition's candidate/destinationRef.

Run:
```bash
npm --prefix /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager test -- --testTimeout=30000 -t "carries a push-pending"
```
Expected: FAIL — `cleanupWorkspace`/`reapLeakedAssignment` currently throw `push-pending obligation`.

**Step 2 — GREEN (carry):** In `workspace-service.ts`:
- Add `insertPreservedWork` to the `./db.js` import (`:5-17`) and `pushPendingSummary` to the
  `./integration.js` import (`:18`).
- In `tombstoneTerminalAssignment`, DELETE the refusal block (`:730-732`):
  ```ts
      if (hasPushPendingObligation(assignment.disposition)) {
        throw new Error('Refusing to tombstone a worktree with a push-pending obligation; resolve or push it first');
      }
  ```
- Replace the final `return transitionAssignment(this.db, assignment.workspace_guid, assignment.lifecycle_status, 'cleaned');`
  (`:743`) with a transactional carry:
  ```ts
    const carried = pushPendingSummary(assignment.disposition);
    return this.db.transaction(() => {
      const cleaned = transitionAssignment(this.db, assignment.workspace_guid, assignment.lifecycle_status, 'cleaned');
      if (carried) {
        insertPreservedWork(this.db, {
          workspaceGuid: assignment.workspace_guid,
          repositoryIdentity: repository.repositoryIdentity,
          ownerSessionId: assignment.owner_session_id,
          kind: 'pending-push',
          payload: JSON.stringify(carried),
        });
      }
      return cleaned;
    })();
  ```
  (`carried` is `undefined` for an abandoned row — disposition NULL — and for `push-succeeded`, so those
  simply transition with no carry. `hasPushPendingObligation` remains imported for its other call site if
  any; if the delete leaves it unused, remove it from the import to satisfy `tsc`.)

**Step 3 — run suite:**
```bash
npm --prefix /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager test -- --testTimeout=30000
```
Expected: all pass (baseline 288 + new/rewritten tests; no `-t` filter).

**Step 4 — typecheck + stage:**
```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npx tsc --noEmit
git -C /Users/roberthyatt/Code/ironclaude add worker/mcp-servers/workspace-manager/src/workspace-service.ts worker/mcp-servers/workspace-manager/src/__tests__/workspace-service.test.ts
```
Expected: tsc exit 0; staged.

---

## Task 2: `closeOutWorktree` handler routes the gone-worktree case

**Depends on:** Task 1. **Files:**
- Modify: `worker/mcp-servers/workspace-manager/src/index.ts` (imports; `closeOutWorktree` `:354`)
- Test: `worker/mcp-servers/workspace-manager/src/__tests__/integration-cases.ts`

**Step 1 — RED (handler gone-worktree completion + present-guard):** In `integration-cases.ts`:
- Add `it('close_out_worktree handler completes DB-only on an integrated row whose worktree is gone (R3)'…)`:
  `seedIntegratedPushPending(s)`, then remove the worktree with
  `git(s.root, 'worktree', 'remove', '--force', s.assignment.worktree_path)`; issue a close-out human
  intent (mirror the existing `close_out_worktree handler closes out an active row` test's
  `issueDirectGitHumanIntent` for `operation:'close-out'`); build `createPublicToolDependencies(db,
  {client:'codex', sessionId: OWNER, invocationThreadId: OWNER, source:'codex_meta'})`; call
  `deps.closeOutWorktree({ repository_path: s.root, workspace_guid: s.assignment.workspace_guid })`.
  Assert `result.state === 'closed-out'`, `result.pendingPush` matches
  `{ candidateCommit: seeded.candidate, destinationRef: seeded.destinationRef }`, the row is `cleaned`,
  a `preserved_work` `pending-push` row exists, and the call did NOT throw a
  `managed workspace Git identity does not match` error.
- Add `it('close_out_worktree handler on an integrated PRESENT worktree still uses the normal path'…)`:
  `seedIntegratedPushPending(s)` (worktree PRESENT), issue the close-out intent, call the handler → assert
  `result.state === 'closed-out'` and the row `cleaned`, AND — the discriminator only the normal path
  satisfies — that the close-out human intent was CONSUMED:
  ```ts
  const intent = s.database.prepare(
    "SELECT consumed_at FROM human_intents WHERE workspace_guid = ? AND operation = 'close-out'"
  ).get(s.assignment.workspace_guid) as { consumed_at: string | null };
  expect(intent.consumed_at).not.toBeNull();
  ```
  Purpose: proves gone-detection does not false-positive — the normal `finalizeCloseOut` path runs AND
  consumes the intent via `consumeMatchingHumanIntent` (git-authority.ts:609-611), whereas the DB-only
  fast path never touches `human_intents`, so a `present`-predicate regression (fast path firing on a live
  worktree) fails here. `state==='closed-out'` + `cleaned` alone cannot discriminate (both paths converge
  on them post-carry). Confirm the seed leaves the worktree present.

Run:
```bash
npm --prefix /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager test -- --testTimeout=30000 -t "worktree is gone (R3)"
```
Expected: FAIL — the handler currently reaches `reconcileFinalization('status')` / `verifyDirectGitAuthority`
on the gone worktree and throws (raw identity / worktree error), not `closed-out`.

**Step 2 — GREEN (handler routing):** In `index.ts`:
- Add `getAssignment` to the `./db.js` import (`:12`) and `worktreeExists` to the `./git.js` import (`:14`).
  (`fs` and `pushPendingSummary` are already imported.)
- In `closeOutWorktree` (`:354`), immediately after `const workspaceGuid = requiredString(args, 'workspace_guid');`
  and BEFORE the `reconcileFinalization('status')` probe, insert:
  ```ts
      // R3: an integrated row whose managed worktree was already removed (crash mid-teardown) cannot
      // mint authority (resolveEffectiveCheckout requires the worktree present). Complete DB-only via
      // the owner-bound cleanupWorkspace (which now carries the obligation); no git op on the gone tree.
      const existing = getAssignment(db, workspaceGuid);
      if (existing && existing.lifecycle_status === 'integrated') {
        const repo = discoverRepository(repositoryPath);
        const present = fs.existsSync(existing.worktree_path)
          && worktreeExists(repo.primaryCheckoutPath, existing.worktree_path);
        if (!present) {
          const carried = pushPendingSummary(existing.disposition);
          const cleaned = service.cleanupWorkspace({ repositoryPath, workspaceGuid, ownerSessionId: identity.sessionId });
          return {
            state: 'closed-out',
            integratedCommit: cleaned.integrated_commit ?? existing.integrated_commit ?? undefined,
            ...(carried ? { pendingPush: carried } : {}),
          };
        }
      }
  ```

**Step 3 — run suite:**
```bash
npm --prefix /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager test -- --testTimeout=30000
```
Expected: all pass including both new handler tests.

**Step 4 — typecheck + stage:**
```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npx tsc --noEmit
git -C /Users/roberthyatt/Code/ironclaude add worker/mcp-servers/workspace-manager/src/index.ts worker/mcp-servers/workspace-manager/src/__tests__/integration-cases.ts
```
Expected: tsc exit 0; staged.
