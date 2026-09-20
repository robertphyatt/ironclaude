# git maxBuffer consistency + reopen-guard robustness — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Make the 64 MB `GIT_MAX_BUFFER` ceiling universal across every workspace-manager git spawn, and fold in the four adjacent reopen-guard / cleanup robustness fixes from the full-corpus Fable end review. `obs6` (C2 disposition-null) is intended and unchanged.

**Requirements:** `docs/plans/2026-09-21-git-maxbuffer-consistency-requirements.md`

**Architecture:** Additive, low-risk. Task 1 adds `maxBuffer: GIT_MAX_BUFFER` to 11 git-spawn sites (git.ts + workspace-service.ts). Task 2 fixes integration.ts: `cumulativeBinaryEffect` error-routing + temp-dir cleanup (B), and the `reopenForEdit` interrupted-CAS guard (C try/catch target, D lock-gate, E message). Task 3 rebuilds dist and runs both suites.

**Tech Stack:** TypeScript (workspace-manager, vitest).

---

## Task 1: Component A — universal maxBuffer (git.ts + workspace-service.ts)

**Files:**
- Modify: `worker/mcp-servers/workspace-manager/src/git.ts`
- Modify: `worker/mcp-servers/workspace-manager/src/workspace-service.ts`

No tests required: defense-in-depth ceiling on git subprocess stdout/stderr; not observable without a >64 MB diff fixture and adds no behavior on bounded-output sites. Verified by the grep check below + the full suite (Task 3) unchanged. `GIT_MAX_BUFFER` is `export const` at git.ts:32 (top-level, in scope within git.ts); workspace-service.ts must import it.

**Step 1:** In `git.ts`, add `maxBuffer: GIT_MAX_BUFFER` to the `spawnSync` options object at each of these 8 sites (currently lacking it): `:55` (`git --version`), `:210` (`symbolic-ref …origin/HEAD`), `:566` (`merge-base --is-ancestor`), `:579` (`diff --no-ext-diff`), `:582` (`patch-id --stable`, alongside its `input`), `:610` (`cherry`), `:693` (`diff --binary`), `:703` (`apply --cached --check --reverse`). Single-line `{ encoding: 'utf8' }` → `{ encoding: 'utf8', maxBuffer: GIT_MAX_BUFFER }`. Do NOT touch the 5 already-covered sites: `spawnSync` at `:67`/`:80`/`:638`/`:649`/`:663` (their `maxBuffer: GIT_MAX_BUFFER` is at `:67`/`:80`/`:640`/`:652`/`:666`).

**Step 2:** In `workspace-service.ts`, add `GIT_MAX_BUFFER,` to the `from './git.js'` import block (the block closing at `:44`) — alphabetically before `isAncestor`.

**Step 3:** In `workspace-service.ts`, add `maxBuffer: GIT_MAX_BUFFER` to the `spawnSync` options at `:1318` (`merge-tree`), `:1331` (`commit-tree`, alongside `env: botEnv`), `:1342` (`update-ref`).

**Step 4 (verify):** `grep -c "maxBuffer: GIT_MAX_BUFFER" worker/mcp-servers/workspace-manager/src/git.ts` → expect **13** (5 pre-existing + 8 added). `grep -c "maxBuffer: GIT_MAX_BUFFER" worker/mcp-servers/workspace-manager/src/workspace-service.ts` → expect **3**. (Current values before edits: 5 and 0.)

**Step 5:** Stage: `git -C /Users/roberthyatt/Code/ironclaude add worker/mcp-servers/workspace-manager/src/git.ts worker/mcp-servers/workspace-manager/src/workspace-service.ts`

---

## Task 2: Components B + C + D + E — integration.ts robustness + tests

**Files:**
- Modify: `worker/mcp-servers/workspace-manager/src/integration.ts`
- Test: `worker/mcp-servers/workspace-manager/src/__tests__/integration-cases.ts`

C, D, E are TDD (RED first). B (cumulativeBinaryEffect obs5) has no unit test — its ENOBUFS/openSync-failure paths need fault injection this suite doesn't do; it relies on the full suite + the existing leaked-temp-dir counter (`integration-cases.ts:52-55`) staying green.

**Step 1 (RED — C & D tests; E extends the existing lineage-117 test):** In `integration-cases.ts` recovery part (after the lineage-117 reopen cases, before `describe('syncWorktreeToTarget')`):

- **C — `reopen_for_edit: an unresolvable integration target does not throw a raw git error; reopen proceeds`.** Seed candidate **+ lock present**, target made unresolvable:
  ```ts
  const { root, database, assignment, frozen } = seedFrozenReadyNoRebase(false);
  git(root, 'update-ref', `refs/ironclaude/finalization/${assignment.workspace_guid}/candidate`, frozen);
  acquireIntegrationLock(database, {
    repositoryIdentity: assignment.repository_identity,
    workspaceGuid: assignment.workspace_guid,
    targetRef: 'refs/heads/main', expectedTarget: git(root, 'rev-parse', 'HEAD'),
  });
  database.prepare("UPDATE assignments SET integration_target = 'nonexistent-target-branch' WHERE workspace_guid = ?").run(assignment.workspace_guid);
  expect(reconcileFinalization(database, { repositoryPath: root, workspaceGuid: assignment.workspace_guid, providerRootSessionId: OWNER, rebaseRecovery: 'reopen_for_edit' }).state).toBe('finalization-reopened-for-edit');
  expect(database.prepare('SELECT 1 FROM integration_locks WHERE repository_identity = ? AND workspace_guid = ?').get(assignment.repository_identity, assignment.workspace_guid)).toBeUndefined();
  ```
  **Deliberate deviation from design §Testing "delete refs/heads/main":** the target is made unresolvable by pointing `integration_target` at a non-existent branch (same `targetRef` input to the guard; avoids leaving the primary checkout on an unborn HEAD). Lock present + `toBeUndefined()` after proves the guard was skipped (obs3 try/catch) and the proceed path ran through the lock deletion at `:2000-2001` — without C's try/catch the `rev-parse` at `:1952` throws raw (the RED), before the lock is consulted.

- **D — `reopen_for_edit: a candidate that equals the target but has NO held lock is not an interrupted-CAS row; reopen proceeds`.** Interrupted-CAS shape (candidate==target) but WITHOUT `acquireIntegrationLock`:
  ```ts
  const { root, database, assignment } = setup(false);
  git(assignment.worktree_path, 'commit', '-m', 'landed');
  const landed = git(assignment.worktree_path, 'rev-parse', 'HEAD');
  git(root, 'update-ref', `refs/ironclaude/finalization/${assignment.workspace_guid}/frozen`, landed);
  git(root, 'merge', '--ff-only', landed);
  git(root, 'update-ref', `refs/ironclaude/finalization/${assignment.workspace_guid}/candidate`, landed);
  database.prepare("UPDATE assignments SET lifecycle_status = 'ready_for_integration' WHERE workspace_guid = ?").run(assignment.workspace_guid);
  expect(reconcileFinalization(database, { repositoryPath: root, workspaceGuid: assignment.workspace_guid, providerRootSessionId: OWNER, rebaseRecovery: 'reopen_for_edit' }).state).toBe('finalization-reopened-for-edit');
  expect(() => git(root, 'rev-parse', '--verify', `refs/ironclaude/finalization/${assignment.workspace_guid}/candidate`)).toThrow();
  ```

- **E — extend the existing lineage-117 C1 lock-held refusal test** (`it('reopen_for_edit: refuses an interrupted-CAS row where the candidate already landed on the target, preserving proof', …)`): change its `.toThrow('integration already landed on the target')` to the **full new message tail** (a superset that retains the old substring and adds text the old message lacks):
  ```ts
  })).toThrow('integration already landed on the target (interrupted-CAS); run reconcile — it will finish the integration or report the repair needed — do not reopen');
  ```

Run RED: `cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npx vitest run src/__tests__/integration-recovery.test.ts -t reopen_for_edit`
Expected RED: **C** fails (`:1952` rev-parse throws raw instead of proceeding); **D** fails (current guard refuses regardless of lock → throws instead of proceeding); **E** fails (message lacks `it will finish the integration … — do not reopen`; observed today: `…run reconcile to complete it, not reopen`). The other lineage-117 reopen cases still pass.

**Step 2 (GREEN — D lock-gate + E message + C try/catch):** Replace the `reopenForEdit` interrupted-CAS guard (`integration.ts:1947-1956`) with:
```ts
let landedCandidate: string | undefined;
try {
  landedCandidate = runGit(exact.primaryCheckoutPath, ['rev-parse', '--verify', `${candidateRef(assignment.workspace_guid)}^{commit}`]).trim();
} catch { /* no candidate ref: not an interrupted-CAS row */ }
if (landedCandidate) {
  let currentTarget: string | undefined;
  try {
    currentTarget = runGit(exact.primaryCheckoutPath, ['rev-parse', '--verify', `${targetRef(assignment)}^{commit}`]).trim();
  } catch { /* unresolvable target: cannot have landed on it — not interrupted-CAS */ }
  const lockHeld = db.prepare('SELECT 1 FROM integration_locks WHERE repository_identity = ? AND workspace_guid = ?')
    .get(assignment.repository_identity, assignment.workspace_guid) !== undefined;
  if (currentTarget === landedCandidate && lockHeld) {
    throw new Error('reopen_for_edit refused: integration already landed on the target (interrupted-CAS); run reconcile — it will finish the integration or report the repair needed — do not reopen');
  }
}
```

**Step 3 (GREEN — B, cumulativeBinaryEffect obs5):** (a) add `gitBufferOverflowError,` to integration.ts's `from './git.js'` import (block `:18-32`), alphabetically after `gitError,` (`:24`). (b) Replace the body of `cumulativeBinaryEffect` (`:312-341`) with this exact shape — routes `result.error` through `gitBufferOverflowError`, hoists `wfd` so the `finally` can close it, and cleans the temp dir even if `openSync` throws; digest byte-exact (spawn args, stdio, read loop, hash unchanged), `wfdOpen` removed (its close-guard becomes `wfd !== undefined`):
```ts
function cumulativeBinaryEffect(cwd: string, base: string, head: string): string {
  const dir = mkdtempSync(path.join(tmpdir(), 'ic-finalize-diff-'));
  const file = path.join(dir, 'diff.bin');
  let wfd: number | undefined;
  try {
    wfd = openSync(file, 'w');
    const args = ['diff', '--binary', '--full-index', base, head];
    const result = spawnSync('git', ['-C', cwd, ...args], { stdio: ['ignore', wfd, 'pipe'], maxBuffer: GIT_MAX_BUFFER });
    closeSync(wfd);
    wfd = undefined;
    if (result.error) {
      const overflow = gitBufferOverflowError(args, result.error);
      throw overflow ?? result.error;
    }
    if (result.status !== 0) throw gitError(cwd, args, (result.stderr || '').toString());
    const hash = createHash('sha256');
    const rfd = openSync(file, 'r');
    try {
      const buf = Buffer.allocUnsafe(1 << 20);
      let n: number;
      while ((n = readSync(rfd, buf, 0, buf.length, null)) > 0) hash.update(buf.subarray(0, n));
    } finally {
      closeSync(rfd);
    }
    return hash.digest('hex');
  } finally {
    if (wfd !== undefined) {
      try { closeSync(wfd); } catch { /* already closed */ }
    }
    try { unlinkSync(file); } catch { /* best-effort: absent if openSync threw */ }
    try { rmdirSync(dir); } catch { /* best-effort */ }
  }
}
```

**Step 4 (GREEN verify):** `cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npx vitest run src/__tests__/integration-recovery.test.ts -t reopen_for_edit`
Expected: 0 failed (C, D, E green; existing lineage-117 reopen cases still pass).

**Step 5:** Stage: `git -C /Users/roberthyatt/Code/ironclaude add worker/mcp-servers/workspace-manager/src/integration.ts worker/mcp-servers/workspace-manager/src/__tests__/integration-cases.ts`

---

## Task 3: Rebuild dist + full-suite verification

**Files:**
- Modify: `worker/mcp-servers/workspace-manager/dist/cli.js`, `worker/mcp-servers/workspace-manager/dist/index.js`, `worker/mcp-servers/workspace-manager/dist/hook-intent.js`

**Depends on:** Task 1, Task 2.

No tests required: build + verification task.

**Step 1:** `cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npm run build` — expect tsc + bundle, no error (tsc also proves the workspace-service.ts `GIT_MAX_BUFFER` import and integration.ts `gitBufferOverflowError` import resolve, and the `cumulativeBinaryEffect` restructure type-checks).

**Step 2:** `cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npx vitest run` — expect `passed | 0 failed` (the leaked-temp-dir counter test still green).

**Step 3:** `cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q` — expect 0 failed.

**Step 4:** Stage dist (force): `git -C /Users/roberthyatt/Code/ironclaude add -f worker/mcp-servers/workspace-manager/dist/cli.js worker/mcp-servers/workspace-manager/dist/index.js worker/mcp-servers/workspace-manager/dist/hook-intent.js`
