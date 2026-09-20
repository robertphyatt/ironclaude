# Interrupted-CAS ancestry recovery + cleanups — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Fix I1 (interrupted-CAS recovery must detect "already landed" by ancestry, not exact equality, so a landed-then-advanced row is completed rather than stranded) and fold in 4 SAFE proportionality cleanups. Folds into unpushed v1.1.11.

**Requirements:** `docs/plans/2026-09-21-interrupted-cas-ancestry-requirements.md`

**Architecture:** C1 widens the crash-recovery admission from equality to ancestry and gates the primary-checkout repair on the exact case (the advancing operation owns primary/ref consistency). Cleanups collapse the daemon marker dicts (C2), fix/delete the associated tests (C3), delete two tautology TS tests (C4), and dedup a recovery-ref helper (C5).

**Tech Stack:** TypeScript (workspace-manager, vitest), Python (commander, pytest).

> **Guard scope (do NOT add a worktreeHead conjunct):** R1 refuses reopen when the candidate landed (`isAncestor(candidate, targetRef)`) AND the lock is held — nothing more. A genuine interrupted-CAS row where the worker committed again post-CAS (worktree HEAD = candidate+N) must still be refused loudly and routed to reconcile (which reports "candidate and source HEAD differ; preserving worktree"); a HEAD gate would let reopen strand it — the exact I1 harm. Backlog (NOT this loop): the stale prior-lifecycle-candidate sub-shape (S1) is discriminated by `isAncestor(candidate, lock.expected_target)` and belongs in reconcile's no-record branch.

---

## Task 1: C1 (I1 ancestry fix) + C5 (persistRecoveryRef dedup) — integration.ts + tests

**Files:**
- Modify: `worker/mcp-servers/workspace-manager/src/integration.ts`
- Test: `worker/mcp-servers/workspace-manager/src/__tests__/integration-cases.ts`

TDD for C1 (two RED cases). C5 is a pure refactor verified by the existing suite. NOTE: the exact-target `advancedWithoutRecord` reconcile-completes case is in the CORE part (`integration-cases.ts:284`, run by `integration-core.test.ts`), so verification runs BOTH the core and recovery test files.

**Step 1 (RED — C1 tests):** In `integration-cases.ts` recovery part (after the existing reopen_for_edit cases, before `describe('syncWorktreeToTarget')`), add two cases. Build a **landed-then-advanced interrupted-CAS** seed — model it on the existing crash-recovery `advancedWithoutRecord` sub-seed (~`:1958-1975`, which already reaches `markIntegrated`): `setup(false)`; commit reviewed work → `landed`; `oldTarget = git(root,'rev-parse','HEAD')`; `update-ref` frozen = `landed`; `acquireIntegrationLock(database,{repositoryIdentity, workspaceGuid, targetRef:'refs/heads/main', expectedTarget: oldTarget})`; `git(root,'merge','--ff-only',landed)`; `update-ref` candidate = `landed`; set `lifecycle_status='ready_for_integration'` (mirror the passing crash seed exactly so the effect proof `cumulativeBinaryEffect(base_commit, frozen) === cumulativeBinaryEffect(oldTarget, landed)` holds — base_commit = initial = oldTarget, frozen = landed). THEN advance the target past candidate: `git(root,'commit','--allow-empty','-m','unrelated advance')` on main.
  - **Case A (reconcile completes):** `expect(reconcileFinalization(database,{repositoryPath:root, workspaceGuid, providerRootSessionId:OWNER}).state).toBe('cleaned')`, `integratedCommit === landed`. RED today: throws `Crash reconciliation target is not the exact candidate`.
  - **Case B (reopen refuses the advanced-landed row):** same seed → `expect(() => reconcileFinalization(database,{…, rebaseRecovery:'reopen_for_edit'})).toThrow('integration already landed on the target')`; assert candidate ref, frozen ref, and the integration_locks row all survive. RED today: the equality guard doesn't fire (target ≠ candidate) → reopen proceeds (no throw).
  - Keep the existing exact-target cases (lineage-117/118) intact.

Run RED: `cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npx vitest run src/__tests__/integration-core.test.ts src/__tests__/integration-recovery.test.ts`
Expected RED: Case A throws "not the exact candidate"; Case B does not throw. Existing core + recovery cases pass.

**Step 2 (GREEN C1 — reconcile crash branch, integration.ts ~:2206-2242):** (a) replace `if (currentTarget !== candidate) throw new Error('Crash reconciliation target is not the exact candidate; preserving worktree');` (~:2208) with:
```ts
if (!isAncestor(exact.primaryCheckoutPath, candidate, currentTarget)) {
  throw new Error('Crash reconciliation candidate did not land on the target; preserving worktree');
}
```
(b) Wrap the existing `try { verifyPrimaryAfterFastForward(exact.primaryCheckoutPath, ref, expectedTarget, candidate); } catch { repairPrimaryCheckoutAfterInterruptedCas(exact.primaryCheckoutPath, ref, expectedTarget, candidate); }` block (~:2215-2219) in `if (currentTarget === candidate) { … }`. Leave `requireExactIntegrationLock`, the effect proof (~:2222-2223), `markIntegrated(candidate, ref)`, the disposition check, and `recycleFinalized` unchanged (all correct for both the exact and advanced cases). The `sourceHead !== candidate` gate at ~:2192 is unchanged and still required (a genuine interrupted-CAS has worktree HEAD === candidate).

**Step 3 (GREEN C1 — reopenForEdit guard, integration.ts ~:1951-1964):** replace the guard body with the ancestry form (subsumes lineage-118's unresolvable-target try/catch — `isAncestor` returns false for a missing/unresolvable ref):
```ts
let landedCandidate: string | undefined;
try {
  landedCandidate = runGit(exact.primaryCheckoutPath, ['rev-parse', '--verify', `${candidateRef(assignment.workspace_guid)}^{commit}`]).trim();
} catch { /* no candidate ref: not an interrupted-CAS row */ }
if (landedCandidate) {
  const landed = isAncestor(exact.primaryCheckoutPath, landedCandidate, targetRef(assignment));
  const lockHeld = db.prepare('SELECT 1 FROM integration_locks WHERE repository_identity = ? AND workspace_guid = ?')
    .get(assignment.repository_identity, assignment.workspace_guid) !== undefined;
  if (landed && lockHeld) {
    throw new Error('reopen_for_edit refused: integration already landed on the target (interrupted-CAS); run reconcile — it will finish the integration or report the repair needed — do not reopen');
  }
}
```
(No `worktreeHead` conjunct — see Guard scope above. `isAncestor` is already imported.)

**Step 4 (GREEN C5 — persistRecoveryRef dedup):** extract `persistRecoveryRef(db, exact, oid, residualFiles)` (create-only `update-ref <recoveryRef> <oid> ''` with same-oid-tolerant catch → `UPDATE assignments SET recovery_ref` → `insertPreservedWork({kind:'recovery', payload:{ref, residualFiles}})` → re-verify `rev-parse --verify <ref>^{commit}`) and call it from both `snapshotResidualIfDirty` (~:1498-1521) and `reopenForEdit`'s clean-diverged-HEAD branch (~:1979-1996). Behavior identical.

**Step 5 (verify):** `cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npx vitest run src/__tests__/integration-core.test.ts src/__tests__/integration-recovery.test.ts` — expect 0 failed (Case A/B green; all existing exact-target + reopen/crash cases pass).

**Step 6 (stage):** `git -C /Users/roberthyatt/Code/ironclaude add worker/mcp-servers/workspace-manager/src/integration.ts worker/mcp-servers/workspace-manager/src/__tests__/integration-cases.ts`

---

## Task 2: C2 (collapse daemon marker dicts) + C3 (fix/delete marker tests) — main.py + commander tests

**Files:**
- Modify: `commander/src/ironclaude/main.py`
- Modify: `commander/tests/test_idle_worker_ttl.py`
- Modify: `commander/tests/test_daemon.py`

Two distinct code sites: the **marker block** (alert + re-arm) is in `check_workers` (~:4451-4487); the **non-running sweep** (clears per-worker dicts for departed workers) is in `check_stuck_workers` (~:4162-4167). C2 touches both plus the inits.

**Step 1 (survey):** `cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_daemon.py -q -k "finalize or marker or reopen"` — baseline (current marker tests pass). Note the marker/re-arm tests (~:5204-5323) and the del-attrs test (~:5325-5347).

**Step 2 (GREEN C2 — main.py):**
- Init (~:1601-1607): replace the `_commit_failure_alerted` / `_commit_reopen_processed` dict inits with `self._finalize_marker_seen: dict[str, int] = {}` and `self._commit_failure_alerted: set[str] = set()`.
- Marker block in `check_workers` (~:4451-4487): remove the `latest_reopen_id` computation (~:4459-4463); replace the re-arm + alert with:
  ```python
  if _latest_marker > self._finalize_marker_seen.get(worker_id, 0):
      self._finalize_marker_seen[worker_id] = _latest_marker
      self._finalize_drift_retry.pop(worker_id, None)
      self._finalize_recovery_alerted.discard(worker_id)
      self._commit_failure_alerted.discard(worker_id)
  if _since >= FINALIZE_DRIFT_RETRY_CAP and worker_id not in self._commit_failure_alerted:
      self._commit_failure_alerted.add(worker_id)
      self.slack.post_message(…unchanged…)
      self.brain.send_message(…unchanged…)
  ```
- Non-running sweep in `check_stuck_workers` (~:4162-4167): replace BOTH loops — `for wid in list(self._commit_failure_alerted): if wid not in running_ids: self._commit_failure_alerted.discard(wid)` and `for wid in list(self._finalize_marker_seen): if wid not in running_ids: self._finalize_marker_seen.pop(wid, None)`. (The existing `:4164` `.pop(wid, None)` on `_commit_failure_alerted` becomes a `TypeError` once it is a set — this is the M1 fix; do NOT leave it.)

**Step 3 (GREEN C3 — tests):** In `test_daemon.py`: delete the del-attrs test (~:5325-5347); port the marker/re-arm tests (~:5204-5323) to the new `_finalize_marker_seen` dict + `_commit_failure_alerted` set (a new integrate OR reopen marker resets the episode; alert once per episode). Add two tests in `TestCommitWorkerFailureSurface`:
  - **M1 sweep falsifier** `test_non_running_sweep_clears_marker_state(self, daemon)`: `daemon._finalize_marker_seen["w1"]=4`; `daemon._commit_failure_alerted.add("w1")`; `daemon.registry.get_running_workers.return_value=[]`; `daemon._last_stuck_check=0`; `daemon.check_stuck_workers()`; assert `"w1" not in daemon._finalize_marker_seen` and `"w1" not in daemon._commit_failure_alerted`. (RED without the `.discard` fix: `set.pop` TypeError.)
  - **Obs2 integrate-marker reset** (clone `test_reopen_rearm_fires_on_new_marker` ~:5295 with a single `finalize_integrated` marker id 4): seed `_finalize_drift_retry["w1"]=2` + `_finalize_recovery_alerted.add("w1")` → `check_workers()` → both cleared; same events again → not re-cleared. (RED if the collapse regresses to reopen-only reset.)
In `test_idle_worker_ttl.py` (~:29, :60 and any other `__new__` fixture reaching `check_workers`/reap paths): replace `daemon._commit_reopen_processed = {}` with `daemon._finalize_marker_seen = {}` and set `daemon._commit_failure_alerted = set()` (was a dict).

**Step 4 (verify):** `cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_daemon.py tests/test_idle_worker_ttl.py -q` — expect 0 failed.

**Step 5 (stage):** `git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/main.py commander/tests/test_idle_worker_ttl.py commander/tests/test_daemon.py`

---

## Task 3: C4 — delete two tautology tests + unused import (git.test.ts)

**Files:**
- Modify: `worker/mcp-servers/workspace-manager/src/__tests__/git.test.ts`

No new tests (deletions).

**Step 1:** Delete the `expect(GIT_MAX_BUFFER).toBe(64 * 1024 * 1024)` tautology test (~:479-481) and the source-text-grep-for-the-literal test (~:513-517). Keep the >1 MB stdout round-trip tests (the real falsifiers). Since those two were the only uses of `GIT_MAX_BUFFER` in this file, also drop `GIT_MAX_BUFFER` from the import at ~:13.

**Step 2 (verify):** `cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npx vitest run src/__tests__/git.test.ts` — expect 0 failed.

**Step 3 (stage):** `git -C /Users/roberthyatt/Code/ironclaude add worker/mcp-servers/workspace-manager/src/__tests__/git.test.ts`

---

## Task 4: Rebuild dist + full-suite verification

**Files:**
- Modify: `worker/mcp-servers/workspace-manager/dist/cli.js`, `worker/mcp-servers/workspace-manager/dist/index.js`, `worker/mcp-servers/workspace-manager/dist/hook-intent.js`

**Depends on:** Task 1, Task 2, Task 3.

No tests required: build + verification.

**Step 1:** `cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npm run build` — expect tsc + bundle, no error.
**Step 2:** `cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npx vitest run` — expect `passed | 0 failed`.
**Step 3:** `cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q` — expect 0 failed.
**Step 4:** `git -C /Users/roberthyatt/Code/ironclaude add -f worker/mcp-servers/workspace-manager/dist/cli.js worker/mcp-servers/workspace-manager/dist/index.js worker/mcp-servers/workspace-manager/dist/hook-intent.js`
