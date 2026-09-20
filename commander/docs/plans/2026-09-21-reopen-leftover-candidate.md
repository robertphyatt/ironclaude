# reopen_for_edit leftover-candidate discriminator (I-1) — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Fix I-1 — the ancestry reopen guard must refuse only a genuine this-lifecycle landed CAS (candidate descends from the lock's `expected_target`), letting a stale prior-lifecycle leftover candidate whose target has advanced proceed to a cleaning reopen. Folds into unpushed v1.1.11 alongside lineage 119.

**Requirements:** `docs/plans/2026-09-21-reopen-leftover-candidate-requirements.md`

**Architecture:** One added conjunct on the reopen guard (`isAncestor(expected_target, candidate)`), reading `expected_target` from the held lock; plus an accurate comment rewrite. TDD.

**Tech Stack:** TypeScript (workspace-manager, vitest).

> **Known gap (backlog, out of scope — scope=hold):** a leftover candidate that still EQUALS `expected_target` (target never advanced between lifecycles, T==C_old) is indistinguishable here from a genuine empty-effect landed CAS and remains refused (reconcile also refuses it at its source-HEAD check). This is PRE-EXISTING under lineage 118's equality guard, not an I-1 regression; the principled discriminator (`sourceHead !== candidate`) also changes the genuine-landed-then-HEAD-moved shape (both verbs currently refuse it with proof preserved), so it is a separate blast-radius decision, not this loop.

---

## Task 1: C1 — expected_target discriminator + test (integration.ts + integration-cases.ts)

**Files:**
- Modify: `worker/mcp-servers/workspace-manager/src/integration.ts`
- Test: `worker/mcp-servers/workspace-manager/src/__tests__/integration-cases.ts`

**Step 1 (RED):** In `integration-cases.ts` recovery part (near the other reopen_for_edit cases), add a test for the stale-leftover-with-advanced-target shape. NOTE `setup()` stages exactly one file, consumed by the first worktree commit, so the second worktree commit MUST be `--allow-empty` (repo convention; a plain `git commit` with a clean index exits 1 and the execFileSync `git()` helper throws):
```ts
it('reopen_for_edit: a stale leftover candidate (ancestor of an advanced expected_target) proceeds to a cleaning reopen', () => {
  const { root, database, assignment } = setup(false);
  // prior lifecycle: C_old landed on main
  git(assignment.worktree_path, 'commit', '-m', 'prior lifecycle work');
  const cOld = git(assignment.worktree_path, 'rev-parse', 'HEAD');
  git(root, 'merge', '--ff-only', cOld);
  git(root, 'update-ref', `refs/ironclaude/finalization/${assignment.workspace_guid}/candidate`, cOld); // leftover
  // target advances past C_old
  git(root, 'commit', '--allow-empty', '-m', 'target advanced past C_old');
  const advancedTarget = git(root, 'rev-parse', 'HEAD');
  // current lifecycle: frozen F (child of C_old), worktree HEAD = F, lock expected_target = advancedTarget
  git(assignment.worktree_path, 'commit', '--allow-empty', '-m', 'current lifecycle reviewed work');
  const frozenF = git(assignment.worktree_path, 'rev-parse', 'HEAD');
  git(root, 'update-ref', `refs/ironclaude/finalization/${assignment.workspace_guid}/frozen`, frozenF);
  acquireIntegrationLock(database, {
    repositoryIdentity: assignment.repository_identity,
    workspaceGuid: assignment.workspace_guid,
    targetRef: 'refs/heads/main', expectedTarget: advancedTarget,
  });
  database.prepare("UPDATE assignments SET lifecycle_status = 'ready_for_integration' WHERE workspace_guid = ?").run(assignment.workspace_guid);

  const result = reconcileFinalization(database, { repositoryPath: root, workspaceGuid: assignment.workspace_guid, providerRootSessionId: OWNER, rebaseRecovery: 'reopen_for_edit' });
  expect(result.state).toBe('finalization-reopened-for-edit');
  expect(() => git(root, 'rev-parse', '--verify', `refs/ironclaude/finalization/${assignment.workspace_guid}/candidate`)).toThrow(); // leftover cleared
  expect(database.prepare('SELECT 1 FROM integration_locks WHERE repository_identity = ? AND workspace_guid = ?').get(assignment.repository_identity, assignment.workspace_guid)).toBeUndefined(); // leaked lock cleared
});
```
Run RED: `cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npx vitest run src/__tests__/integration-recovery.test.ts`
Expected RED: this case fails because the current lineage-119 guard (`landed && lockHeld`) REFUSES it (`isAncestor(C_old, advancedTarget)` true + lock held) — it throws `reopen_for_edit refused` instead of proceeding. The lineage-119 "reopen refuses a landed candidate whose target has advanced" case still passes.

**Step 2 (GREEN):** In `reopenForEdit`'s interrupted-CAS guard (~:1961-1977), replace the `SELECT 1 …` lockHeld check and the `if (landed && lockHeld)` refusal with the expected_target discriminator, and replace the comment block (this plan's comment wording SUPERSEDES the design doc's illustrative block, which overclaims):
```ts
let landedCandidate: string | undefined;
try {
  landedCandidate = runGit(exact.primaryCheckoutPath, ['rev-parse', '--verify', `${candidateRef(assignment.workspace_guid)}^{commit}`]).trim();
} catch { /* no candidate ref: not an interrupted-CAS row */ }
if (landedCandidate) {
  const landed = isAncestor(exact.primaryCheckoutPath, landedCandidate, targetRef(assignment));
  const lockRow = db.prepare('SELECT expected_target FROM integration_locks WHERE repository_identity = ? AND workspace_guid = ?')
    .get(assignment.repository_identity, assignment.workspace_guid) as { expected_target: string } | undefined;
  // Refuse only a GENUINE this-lifecycle landed CAS: the candidate is reachable from the
  // target, the integration lock is held, AND the candidate descends from the lock's
  // expected_target (the CAS wrote candidate = expected_target + reviewed content). A stale
  // prior-lifecycle leftover candidate whose target has since advanced is a STRICT ancestor
  // of expected_target — it predates this lock — so it proceeds to a cleaning reopen instead
  // of stranding. Known gap: a leftover that still EQUALS expected_target (target never
  // advanced between lifecycles) is indistinguishable here from a landed empty-effect CAS
  // and is still refused; reconcile also refuses it at its source-HEAD check.
  // Refusal routes to reconcile, which completes the genuine landed row via markIntegrated.
  if (landed && lockRow && isAncestor(exact.primaryCheckoutPath, lockRow.expected_target, landedCandidate)) {
    throw new Error('reopen_for_edit refused: integration already landed on the target (interrupted-CAS); run reconcile — it will finish the integration or report the repair needed — do not reopen');
  }
}
```

**Step 3 (verify):** `cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npx vitest run src/__tests__/integration-core.test.ts src/__tests__/integration-recovery.test.ts` — expect 0 failed (the new leftover case proceeds; the lineage-119 genuine-landed-then-advanced reopen-refuses + reconcile-completes cases and all others still pass).

**Step 4 (stage):** `git -C /Users/roberthyatt/Code/ironclaude add worker/mcp-servers/workspace-manager/src/integration.ts worker/mcp-servers/workspace-manager/src/__tests__/integration-cases.ts`

---

## Task 2: Rebuild dist + full-suite verification

**Files:**
- Modify: `worker/mcp-servers/workspace-manager/dist/cli.js`, `worker/mcp-servers/workspace-manager/dist/index.js`, `worker/mcp-servers/workspace-manager/dist/hook-intent.js`

**Depends on:** Task 1.

No tests required: build + verification.

**Step 1:** `cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npm run build` — expect tsc + bundle, no error.
**Step 2:** `cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npx vitest run` — expect `passed | 0 failed`.
**Step 3:** `cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q` — expect 0 failed.
**Step 4:** `git -C /Users/roberthyatt/Code/ironclaude add -f worker/mcp-servers/workspace-manager/dist/cli.js worker/mcp-servers/workspace-manager/dist/index.js worker/mcp-servers/workspace-manager/dist/hook-intent.js`
