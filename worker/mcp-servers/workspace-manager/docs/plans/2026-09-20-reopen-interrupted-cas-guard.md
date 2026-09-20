# reopen_for_edit interrupted-CAS guard — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Refuse `reopen_for_edit` on an interrupted-CAS row (C1/MATERIAL), null `assignments.disposition` on reopen (C2/O2), and list `reopen_for_edit` in the Brain-visible recovery-action docs (C3/O4).

**Requirements:** `worker/mcp-servers/workspace-manager/docs/plans/2026-09-20-reopen-interrupted-cas-guard-requirements.md`

**Architecture:** C1 adds a pre-mutation refusal branch at the top of `reopenForEdit` mirroring `reconcileFinalization`'s crash-recovery candidate/target resolution; C2 adds `setDisposition(db, guid, null)` to reopen's existing transaction; C3 edits two Python docstrings. Never-discard-work is preserved: the C1 refusal keeps the interrupted-CAS proof (candidate/freeze refs + lock) intact and routes to plain reconcile.

**Tech Stack:** TypeScript (workspace-manager, vitest), Python (commander).

> **Docs location note:** design/requirements/plan docs live under `worker/mcp-servers/workspace-manager/docs/plans/` (the professional-mode-guard's writable docs base at the current Bash cwd). Relocation to `commander/docs/plans/` is dropped — no task owns it, and `design_file` must string-match the registered design path. `allowed_files` are git-root-relative regardless.

---

## Task 1: C1 interrupted-CAS refusal + C2 null disposition (integration.ts + tests)

**Files:**
- Modify: `worker/mcp-servers/workspace-manager/src/integration.ts`
- Test: `worker/mcp-servers/workspace-manager/src/__tests__/integration-cases.ts`

`integration-cases.ts` is not a standalone vitest file — it exports `registerFinalizationTests('core'|'recovery')` (`:57`); the `reopen_for_edit` cases live in the **recovery** part (`describe('recovery integration')`, `:2255`, gated `part === 'recovery'`), registered by `integration-recovery.test.ts`. So the new cases go in the recovery part and are run via `integration-recovery.test.ts`.

**Step 1 (RED):** In `integration-cases.ts`, immediately AFTER the last existing `reopen_for_edit` case (its closing `});` at `:2816`) and BEFORE `describe('syncWorktreeToTarget')` (`:2818`), add two `it(...)` cases whose names begin with `reopen_for_edit:` (so `-t reopen_for_edit` selects them):

- **C1 — `reopen_for_edit: refuses an interrupted-CAS row where the candidate already landed on the target, preserving proof`.** Build the interrupted-CAS shape inline (the `advancedWithoutRecord` construction, `:1958-1971`): `const { root, database, assignment } = setup(false);` → `git(assignment.worktree_path,'commit','-m','landed work')` → `const landed = git(assignment.worktree_path,'rev-parse','HEAD')` → `const oldTarget = git(root,'rev-parse','HEAD')` → `git(root,'update-ref',`refs/ironclaude/finalization/${assignment.workspace_guid}/frozen`, landed)` → `acquireIntegrationLock(database,{repositoryIdentity:assignment.repository_identity,workspaceGuid:assignment.workspace_guid,targetRef:'refs/heads/main',expectedTarget:oldTarget})` → `git(root,'merge','--ff-only',landed)` (target main now == landed == candidate) → `git(root,'update-ref',`refs/ironclaude/finalization/${assignment.workspace_guid}/candidate`, landed)` → `database.prepare("UPDATE assignments SET lifecycle_status='ready_for_integration' WHERE workspace_guid = ?").run(assignment.workspace_guid)`. Then:
  ```
  expect(() => reconcileFinalization(database, { repositoryPath: root, workspaceGuid: assignment.workspace_guid, providerRootSessionId: OWNER, rebaseRecovery: 'reopen_for_edit' }))
    .toThrow('integration already landed on the target');
  ```
  Assert the proof SURVIVES: candidate ref (`git(root,'rev-parse','--verify',`refs/ironclaude/finalization/${assignment.workspace_guid}/candidate`)` === landed), frozen ref (=== landed), lock (`database.prepare('SELECT 1 FROM integration_locks WHERE repository_identity = ? AND workspace_guid = ?').get(assignment.repository_identity, assignment.workspace_guid)` is defined), lifecycle still `ready_for_integration`.

- **C2 — `reopen_for_edit: nulls a carried integration-pending disposition on reopen`.** `const { root, database, assignment, frozen } = seedFrozenReadyNoRebase(false);` (frozen-no-rebase ready row, target≠candidate, NO candidate ref → C1 guard skipped). Set a carried disposition and pre-assert it (falsifiability):
  ```
  database.prepare('UPDATE assignments SET disposition = ? WHERE workspace_guid = ?').run(JSON.stringify({
    phase: 'integration-pending', frozenCommit: frozen, remoteName: 'origin',
    remoteUrl: 'file:///unused-remote', destinationRef: `refs/heads/${assignment.branch}`, expectedRemoteOldOid: null,
  }), assignment.workspace_guid);
  expect(database.prepare('SELECT disposition FROM assignments WHERE workspace_guid = ?').get(assignment.workspace_guid))
    .toMatchObject({ disposition: expect.stringContaining('integration-pending') });
  const result = reconcileFinalization(database, { repositoryPath: root, workspaceGuid: assignment.workspace_guid, providerRootSessionId: OWNER, rebaseRecovery: 'reopen_for_edit' });
  expect(result.state).toBe('finalization-reopened-for-edit');
  expect(database.prepare('SELECT disposition, lifecycle_status FROM assignments WHERE workspace_guid = ?').get(assignment.workspace_guid))
    .toMatchObject({ disposition: null, lifecycle_status: 'active' });
  ```
  (`remoteUrl` is a literal — nothing on the reopen path resolves it; `setup(false)` has no `origin` remote, so `git remote get-url origin` must NOT be used.)

Run: `cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npx vitest run src/__tests__/integration-recovery.test.ts -t reopen_for_edit`
Expected: RED — C1 does NOT throw (without the guard reopen returns `finalization-reopened-for-edit` and strips candidate/frozen refs + lock, so `toThrow` fails); C2 disposition is still the JSON, not null. (The existing 5 `reopen_for_edit` cases still pass.)

**Step 2 (GREEN C1):** At the TOP of `reopenForEdit` (`integration.ts:1936`), before `classifyRebaseState`/any mutation, add:
```ts
// Interrupted-CAS refusal: the finalization already landed on the target
// (candidate ref resolves AND target ref equals it) — the crash-recovery shape
// reconcileFinalization completes via markIntegrated using the candidate + held
// lock. Reopening would delete that proof (candidate/freeze refs + lock) and
// strand an already-integrated assignment. Refuse before any mutation.
let landedCandidate: string | undefined;
try {
  landedCandidate = runGit(exact.primaryCheckoutPath, ['rev-parse', '--verify', `${candidateRef(assignment.workspace_guid)}^{commit}`]).trim();
} catch { /* no candidate ref: not an interrupted-CAS row */ }
if (landedCandidate) {
  const currentTarget = runGit(exact.primaryCheckoutPath, ['rev-parse', '--verify', `${targetRef(assignment)}^{commit}`]).trim();
  if (currentTarget === landedCandidate) {
    throw new Error('reopen_for_edit refused: integration already landed on the target (interrupted-CAS); run reconcile to complete it, not reopen');
  }
}
```
(`assignment` is `exact.assignment`, already bound at `:1940`.)

**Step 3 (GREEN C2):** In `reopenForEdit`'s `db.transaction` (`:1979-1981`), add `setDisposition` alongside the transition:
```ts
db.transaction(() => {
  transitionAssignment(db, assignment.workspace_guid, 'ready_for_integration', 'active');
  setDisposition(db, assignment.workspace_guid, null);
})();
```

**Step 4 (GREEN verify):** Run `cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npx vitest run src/__tests__/integration-recovery.test.ts -t reopen_for_edit`
Expected: 0 failed (both new cases GREEN; the existing 5 reopen cases still pass).

**Step 5:** Stage: `git -C /Users/roberthyatt/Code/ironclaude add worker/mcp-servers/workspace-manager/src/integration.ts worker/mcp-servers/workspace-manager/src/__tests__/integration-cases.ts`

---

## Task 2: C3 — list reopen_for_edit in Brain-visible recovery docs (orchestrator_mcp.py)

**Files:**
- Modify: `commander/src/ironclaude/orchestrator_mcp.py`

No tests required: doc/prompt-only change to two Python docstrings; `_RECOVERY_ACTIONS` (`:3968`) already accepts `reopen_for_edit`.

**Step 1:** In `recover_worker_integration`'s method docstring (`:4172-4175`), after the `rerebase`/`restore_frozen` (recover a drifted frozen worktree) clause, append: `, or 'reopen_for_edit' (return a stuck reviewed row to editable 'active', preserving work)`.

**Step 2:** In the `@mcp.tool()` `recover_worker_integration` description (`:7385-7388`), make the same addition after the `rerebase`/`restore_frozen` clause.

**Step 3:** Verify: `grep -n "reopen_for_edit" /Users/roberthyatt/Code/ironclaude/commander/src/ironclaude/orchestrator_mcp.py` — expect **4 matches**: the existing `_RECOVERY_ACTIONS` line (`:3968`), the existing `action == "reopen_for_edit"` body hit (currently `:4276`, shifts down by the lines inserted at `:4172-4175`), and the two new docstring lines.

**Step 4:** Stage: `git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/orchestrator_mcp.py`

---

## Task 3: Rebuild dist + full-suite verification

**Files:**
- Modify: `worker/mcp-servers/workspace-manager/dist/cli.js`, `worker/mcp-servers/workspace-manager/dist/index.js`, `worker/mcp-servers/workspace-manager/dist/hook-intent.js`

**Depends on:** Task 1, Task 2.

No tests required: build + verification task (the tests it runs are the suites).

**Step 1:** Rebuild dist: `cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npm run build` — expect tsc + bundle, no error.

**Step 2:** Full vitest: `cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npx vitest run` — expect `passed | 0 failed`.

**Step 3:** Full commander pytest: `cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q` — expect 0 failed.

**Step 4:** Stage dist (force; dist is gitignored): `git -C /Users/roberthyatt/Code/ironclaude add -f worker/mcp-servers/workspace-manager/dist/cli.js worker/mcp-servers/workspace-manager/dist/index.js worker/mcp-servers/workspace-manager/dist/hook-intent.js`
