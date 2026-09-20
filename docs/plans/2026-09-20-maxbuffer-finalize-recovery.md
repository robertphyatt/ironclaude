# maxBuffer + Deterministic-Finalize-Failure Recovery — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Close the df69c3a4-class finalize deadlock — make the finalize integrity check content-size independent (so any legitimate commit finalizes on every finalize path), bound the git auxiliary buffers, and give a deterministic finalize failure a seam-independent daemon surface, a Brain give-up bound, and a sanctioned `reopen_for_edit` forward path.

**Requirements:** docs/plans/2026-09-20-maxbuffer-finalize-recovery-requirements.md

**Design:** docs/plans/2026-09-20-maxbuffer-finalize-recovery-design.md

**Architecture:** Component A changes the BODY of `cumulativeBinaryEffect` (the byte-exact reviewed-vs-rebased equality operand) to stream each `git diff --binary --full-index` to a temp file and return its sha256 digest — the name/signature are unchanged, so all eight existing call sites become content-size independent with no call-site edits — plus a shared `GIT_MAX_BUFFER=64MB` net on `runGit`/`runGitEnv` and an exported `gitBufferOverflowError` helper. Components B/C/D close the general deterministic-finalize deadlock: B adds a `reopen_for_edit` reconcile mode (snapshot-if-dirty, reset to frozen, roll `ready_for_integration → active`, delete refs, release lock) exposed through the `reconcile` verb + `_RECOVERY_ACTIONS` + the MCP `reconcile_finalization` tool; C feeds `commit_worker` finalize failures into the persistent per-worker event log with integrate/reopen markers so the daemon surfaces a persistently-failing live worker independent of the idle/reap seams and re-arms after a marker; D adds the Brain give-up bound + 6d terminal branch. Invariant: never auto-abandon, never discard work.

**Tech Stack:** TypeScript (workspace-manager, vitest), Python (commander daemon/orchestrator, pytest), Brain rules markdown.

**Execution invariants:** Shell state does NOT persist between steps — use literal absolute paths. Bash cwd is `commander/`; use `git -C /Users/roberthyatt/Code/ironclaude` and absolute paths. `docs/` is gitignored (`git add -f`). Quote globs. `PYTHONUNBUFFERED=1` on pytest. vitest may emit a benign `onTaskUpdate` RPC timeout under the heavy real-git suite — judge by "N passed | 0 failed", not exit code. `npm run build` = `tsc && npm run bundle`, so a TS type error fails the build even though vitest (esbuild) strips types — every TS task ends with `npx tsc --noEmit` to catch type breaks at the task boundary, not at Task 6. No version bump; no commit/push (operator-gated); no trailers. The workspace-manager `dist/` is tracked-but-gitignored and RUN by the daemon/Brain — rebuild it in Task 6 (`git add -f`).

---

## Task 1: Component A — content-size-independent finalize equality check + bounded auxiliary git buffers

**Files:**
- Modify: `worker/mcp-servers/workspace-manager/src/git.ts`
- Modify: `worker/mcp-servers/workspace-manager/src/integration.ts`
- Modify: `worker/mcp-servers/workspace-manager/src/__tests__/git.test.ts`
- Modify: `worker/mcp-servers/workspace-manager/src/__tests__/integration-cases.ts`

**Step 1 (RED — git.ts buffer net + overflow helper):** In `git.test.ts` add: (a) `runGit` returns a >1MB git stdout in full (create a temp repo, commit ~1.5MB ASCII file, `runGit(repo, ['show', 'HEAD:big.txt']).length >= 1_500_000`, no throw); (b) the SAME for `runGitEnv` (pass `{ ...process.env }`); (c) `GIT_MAX_BUFFER === 64 * 1024 * 1024`; (d) a unit test for the exported helper `gitBufferOverflowError(args, error)` — returns an Error whose message contains the joined argv for a synthetic `Object.assign(new Error('x'), { code: 'ENOBUFS' })`, and `undefined` for a non-ENOBUFS error; (e) a source-text assertion that `git.ts` contains exactly one `64 * 1024 * 1024` literal (the constant definition).

Run:
```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npx vitest run src/__tests__/git.test.ts
```
Expected: RED — `gitBufferOverflowError` is not a function; `GIT_MAX_BUFFER` undefined; the >1MB reads throw ENOBUFS; multiple `64 * 1024 * 1024` literals exist.

**Step 2 (RED — finalize equality is content-size independent + temp cleanup):** In `integration-cases.ts` add a `registerFinalizationTests('recovery')` case that finalizes a commit whose `--binary --full-index` diff exceeds 1MB and asserts it integrates (equality holds); a companion asserting a divergent rebased effect is still refused with the existing message `Finalization rebased cumulative effect differs from reviewed content; preserving worktree`; and, in both, that the count of `ic-finalize-diff-*` entries in `tmpdir()` is equal before and after (temp files cleaned up, including on the refused path). Write the >1MB file into the worktree before committing.

Run:
```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npx vitest run src/__tests__/integration-recovery.test.ts
```
Expected: RED — the large-diff finalize throws ENOBUFS via the current no-maxBuffer `cumulativeBinaryEffect`.

**Step 3 (GREEN — git.ts):** Add at module scope:
```ts
export const GIT_MAX_BUFFER = 64 * 1024 * 1024;

export function gitBufferOverflowError(args: readonly string[], error: NodeJS.ErrnoException | undefined): Error | undefined {
  if (error && error.code === 'ENOBUFS') {
    return new Error(`git ${args.join(' ')} exceeded the ${GIT_MAX_BUFFER}-byte output buffer (ENOBUFS)`);
  }
  return undefined;
}
```
In `runGit` (`:46`) and `runGitEnv` (`:57`): set `maxBuffer: GIT_MAX_BUFFER` on the `spawnSync` options; before `if (result.error) throw result.error;` add:
```ts
  const overflow = gitBufferOverflowError(args, result.error as NodeJS.ErrnoException | undefined);
  if (overflow) throw overflow;
```
Replace the three literal `64 * 1024 * 1024` in `patchIdAggregateTellMerged` (`:615/627/641`) with `GIT_MAX_BUFFER` (leaving the sole literal in the constant definition).

**Step 4 (GREEN — integration.ts):** Change ONLY the body of `cumulativeBinaryEffect` (`:295-297`) — keep its name and signature `(cwd, base, head): string` so all eight call sites (`:849, :858, :1517, :1519, :1650, :1652, :2081, :2082`) are untouched and become content-size independent. New body writes the diff to a temp file via a stdout fd (git writes to disk, never a Node stdout buffer) and returns the sha256 hex of a chunked streaming read:
```ts
function cumulativeBinaryEffect(cwd: string, base: string, head: string): string {
  const dir = mkdtempSync(path.join(tmpdir(), 'ic-finalize-diff-'));
  const file = path.join(dir, 'diff.bin');
  const wfd = openSync(file, 'w');
  try {
    const result = spawnSync('git', ['-C', cwd, 'diff', '--binary', '--full-index', base, head], {
      stdio: ['ignore', wfd, 'pipe'],
      maxBuffer: GIT_MAX_BUFFER,
    });
    closeSync(wfd);
    if (result.error) throw result.error;
    if (result.status !== 0) throw gitError(cwd, ['diff', '--binary', '--full-index', base, head], (result.stderr || '').toString());
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
    try { closeSync(wfd); } catch { /* already closed */ }
    try { unlinkSync(file); } catch { /* best-effort */ }
    try { rmdirSync(dir); } catch { /* best-effort */ }
  }
}
```
Imports (verify against the current `:1-35` block and add only what is missing): `import { spawnSync } from 'node:child_process';`; `import { createHash } from 'node:crypto';`; extend the `node:fs` import to include `closeSync, mkdtempSync, openSync, readSync, unlinkSync, rmdirSync` (keep the existing members); use the already-imported default `path` (`path.join`) and the already-imported `tmpdir` from `node:os`; add `gitError, GIT_MAX_BUFFER` to the `./git.js` import list. Update the doc comment at `:295` to say it returns a sha256 digest of the binary diff (an opaque equality operand), not the diff text. Do NOT rename and do NOT edit any call site.

**Step 5 (verify GREEN + type-check):**
```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npx tsc --noEmit && npx vitest run src/__tests__/git.test.ts src/__tests__/integration-recovery.test.ts src/__tests__/integration-core.test.ts
```
Expected: `tsc --noEmit` exits 0 (no output); vitest "passed | 0 failed".

**Step 6 (stage):**
```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/mcp-servers/workspace-manager/src/git.ts worker/mcp-servers/workspace-manager/src/integration.ts worker/mcp-servers/workspace-manager/src/__tests__/git.test.ts worker/mcp-servers/workspace-manager/src/__tests__/integration-cases.ts
```

---

## Task 2: Component B (TS) — `reopen_for_edit` reconcile mode

**Files:**
- Modify: `worker/mcp-servers/workspace-manager/src/integration.ts`
- Modify: `worker/mcp-servers/workspace-manager/src/cli.ts`
- Modify: `worker/mcp-servers/workspace-manager/src/index.ts`
- Modify: `worker/mcp-servers/workspace-manager/src/__tests__/integration-cases.ts`

**Depends on:** Task 1 (serializes `integration.ts` edits).

**Step 1 (RED — behavior + refusals + falsifiable cleanup):** In `integration-cases.ts` add `registerFinalizationTests('recovery')` cases:
- **Happy path (clean):** from `seedFrozenReadyNoRebase(false)`, ALSO seed a real lock and candidate ref so the deletions are falsifiable: `git(root, 'update-ref', `refs/ironclaude/finalization/${assignment.workspace_guid}/candidate`, frozen)` and `acquireIntegrationLock(database, { repositoryIdentity: assignment.repository_identity, workspaceGuid: assignment.workspace_guid, targetRef: 'refs/heads/main', expectedTarget: git(root,'rev-parse','HEAD') })`. Pre-assert both exist. Call `reconcileFinalization(database, { repositoryPath: root, workspaceGuid: assignment.workspace_guid, providerRootSessionId: OWNER, rebaseRecovery: 'reopen_for_edit' })` and assert: `state === 'finalization-reopened-for-edit'`; assignment `lifecycle_status === 'active'`; frozen and candidate refs gone (`rev-parse --verify` non-zero); worktree HEAD === frozen; no `integration_locks` row for the workspace.
- **Dirty worktree (never discard):** after seeding, write an untracked file and modify a tracked file in the worktree, then invoke `reopen_for_edit`; assert `result.recovery.ref` resolves (`rev-parse --verify`), worktree HEAD === frozen, and a `preserved_work` row with `kind='recovery'` exists for the workspace.
- **Wrong-state refusals:** a paused-rebase row (reuse the conflict seed near `:195`) → `toThrow('reopen_for_edit requires a frozen, no-rebase ready row')`; an `active` (non-ready) row → `toThrow('No ready finalization is available')`.
- Extend the CLI validator test at `:2706-2717` to include `'reopen_for_edit'` in the accepted-modes loop.

Run:
```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npx vitest run src/__tests__/integration-recovery.test.ts
```
Expected: RED — `reopen_for_edit` rejected by `optionalRebaseRecovery` / not dispatched.

**Step 2 (GREEN — unions + validators):**
- `integration.ts:100` (input union): add `| 'reopen_for_edit'`.
- `integration.ts:38-42` (`FinalizationResult.state` union): add `| 'finalization-reopened-for-edit'`.
- `cli.ts:72` return-type union and `cli.ts:75-77`: add `&& value !== 'reopen_for_edit'` to the guard and `'reopen_for_edit'` to the error string.
- `index.ts` `optionalMode` (`:256-264`, the MCP `reconcile_finalization` tool validator): add `'reopen_for_edit'` to its accepted set and type union (bounded widening — keeps the worker-side MCP tool consistent with the CLI).

**Step 3 (GREEN — dispatch + handler):** In `reconcileFinalization`, add a dispatch branch alongside the no-paused-rebase modes (near `:1983-1986`), BEFORE the paused-rebase guard and AFTER the ready-state guard (so an `active` row already throws `No ready finalization is available`):
```ts
  if (input.rebaseRecovery === 'reopen_for_edit') {
    return reopenForEdit(db, exact);
  }
```
Add the handler near `recoverNoPausedRebase` (`:1901`), preserving uncommitted work via the existing `snapshotResidualIfDirty` (`:1428-1477`):
```ts
function reopenForEdit(
  db: Database.Database,
  exact: { assignment: Assignment; primaryCheckoutPath: string },
): FinalizationResult {
  const assignment = exact.assignment;
  const worktree = assignment.worktree_path;
  const state = classifyRebaseState(worktree);
  if (state !== 'frozen-no-rebase') {
    throw new Error('reopen_for_edit requires a frozen, no-rebase ready row; resolve any paused rebase via continue/abort first; preserving worktree');
  }
  const frozen = runGit(exact.primaryCheckoutPath, ['rev-parse', '--verify', `${freezeRef(assignment.workspace_guid)}^{commit}`]).trim();
  const recovery = snapshotResidualIfDirty(db, exact); // undefined when clean; else mints refs/ironclaude/recovery/... + preserved_work row, then resets
  runGit(worktree, ['reset', '--hard', frozen]);
  if (worktreeHead(worktree) !== frozen) {
    throw new Error('reopen_for_edit did not restore the frozen reviewed commit; preserving worktree');
  }
  db.transaction(() => {
    transitionAssignment(db, assignment.workspace_guid, 'ready_for_integration', 'active');
  })();
  try { runGit(exact.primaryCheckoutPath, ['update-ref', '-d', candidateRef(assignment.workspace_guid)]); } catch { /* candidate may be absent */ }
  try { runGit(exact.primaryCheckoutPath, ['update-ref', '-d', freezeRef(assignment.workspace_guid)]); } catch { /* freeze may be absent */ }
  db.prepare('DELETE FROM integration_locks WHERE repository_identity = ? AND workspace_guid = ?')
    .run(assignment.repository_identity, assignment.workspace_guid);
  return { state: 'finalization-reopened-for-edit', detail: 'Uncommitted work snapshotted if any; worktree reset to the frozen reviewed commit; assignment returned to active for re-staging; finalization refs and lock cleared.', recovery };
}
```
(Verify against source before wiring: open `snapshotResidualIfDirty` (`:1428-1477`) and confirm its exact signature and that its return value matches the `recovery` shape on `FinalizationResult.recovery` (`:49`); confirm `classifyRebaseState`, `worktreeHead`, `freezeRef`, `candidateRef`, `transitionAssignment`, `Assignment`, `FinalizationResult` are in scope. `snapshotResidualIfDirty` snapshots any dirty bytes into a recovery ref then itself resets the worktree when dirty, and is a no-op returning `undefined` when clean; the subsequent `runGit(worktree, ['reset', '--hard', frozen])` is idempotent and authoritative — it lands the worktree at `frozen` in both the clean and just-snapshotted cases (a `reset --hard` to a fixed commit is safe to run after the snapshot's own reset). Keep it so the `worktreeHead===frozen` post-condition holds regardless of snapshot's internal behavior.)

**Step 4 (verify GREEN + type-check):**
```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npx tsc --noEmit && npx vitest run src/__tests__/integration-recovery.test.ts src/__tests__/cli.test.ts src/__tests__/tool-dispatch.test.ts
```
Expected: `tsc --noEmit` exits 0; vitest "passed | 0 failed".

**Step 5 (stage):**
```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/mcp-servers/workspace-manager/src/integration.ts worker/mcp-servers/workspace-manager/src/cli.ts worker/mcp-servers/workspace-manager/src/index.ts worker/mcp-servers/workspace-manager/src/__tests__/integration-cases.ts
```

---

## Task 3: Component B (Python) — expose `reopen_for_edit` via `_RECOVERY_ACTIONS`

**Files:**
- Modify: `commander/src/ironclaude/orchestrator_mcp.py`
- Modify: `commander/tests/test_orchestrator_mcp.py`

**Depends on:** Task 2 (the TS mode must exist).

**Step 1 (RED):** In `test_orchestrator_mcp.py`, add a test that `recover_worker_integration(worker_id, 'reopen_for_edit')` is accepted (not rejected by the `_RECOVERY_ACTIONS` guard) and forwards `rebase_recovery='reopen_for_edit'` on the `reconcile` verb (mock `_workspace_client.reconcile`, follow the existing recover_worker_integration test pattern).

Run:
```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_orchestrator_mcp.py -k reopen_for_edit -q
```
Expected: RED — `recovery action is not allowed: reopen_for_edit`.

**Step 2 (GREEN):** In `orchestrator_mcp.py:3957-3959`, add `"reopen_for_edit"` to `_RECOVERY_ACTIONS`. (The non-terminal result state `'finalization-reopened-for-edit'` is not in `_FINALIZATION_INTEGRATED_STATES` (`:2958-2960`), so the worker is correctly NOT marked completed — no other change needed here.)

**Step 3 (verify GREEN):**
```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_orchestrator_mcp.py -k reopen_for_edit -q
```
Expected: pass.

**Step 4 (stage):**
```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/orchestrator_mcp.py commander/tests/test_orchestrator_mcp.py
```

---

## Task 4: Component C — seam-independent daemon surface with integrate/reopen markers

**Files:**
- Modify: `commander/src/ironclaude/orchestrator_mcp.py`
- Modify: `commander/src/ironclaude/main.py`
- Modify: `commander/tests/test_orchestrator_mcp.py`
- Modify: `commander/tests/test_daemon.py`

**Depends on:** Task 3 (serializes `orchestrator_mcp.py` edits).

**Design of the count (R2 MUST-clear):** `finalize_failed` events are append-only, so the daemon counts only failures **since the worker's last integrate/reopen marker**. The orchestrator writes marker events; the daemon counts failures with a higher event id than the newest marker, and re-arms the alert per marker.

**Step 1 (RED — orchestrator records failure + markers):** In `test_orchestrator_mcp.py` add tests: (a) `commit_worker` records a `finalize_failed` event when the finalize raises and classifies as `failure_phase=='finalization'`; (b) `commit_worker` success records a `finalize_integrated` event; (c) `recover_worker_integration` records `finalize_integrated` when the result state is in `_FINALIZATION_INTEGRATED_STATES`, and `finalize_reopened` when `action=='reopen_for_edit'` and the result state is `'finalization-reopened-for-edit'`.

Run:
```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_orchestrator_mcp.py -k "finalize_failed_event or finalize_marker" -q
```
Expected: RED — no such events recorded.

**Step 2 (GREEN — orchestrator):**
- `commit_worker` finalize `except` (`:3416-3419`): capture the classified result; if `isinstance(dict)` and `failure_phase=='finalization'`, `self.registry.log_event("finalize_failed", worker_id=worker_id)`; return the classified dict. On the success path (`:3420`, before `update_worker_status("completed")`), `self.registry.log_event("finalize_integrated", worker_id=worker_id)`.
- `recover_worker_integration` integrated branch (`:4247-4255`): `self.registry.log_event("finalize_integrated", worker_id=worker_id)`. Before returning, if `action == 'reopen_for_edit'` and `isinstance(result, dict)` and `result.get('state') == 'finalization-reopened-for-edit'`: `self.registry.log_event("finalize_reopened", worker_id=worker_id)`.

**Step 3 (RED — daemon seam-independent surface, marker-aware):** In `test_daemon.py` add tests (set `registry.get_events_for_worker` explicitly — the fixture registry is a `MagicMock`, so `len(MagicMock())` is 0 by default): (a) a running, live, non-idle, non-dead worker with 3 `finalize_failed` events and NO later marker → `check_workers` fires `slack.post_message` + `brain.send_message` ONCE and does NOT re-fire on a second cycle; (b) 3 `finalize_failed` then a later `finalize_integrated` then 1 `finalize_failed` → no fire (count since marker = 1); (c) fire once, then a later `finalize_reopened`, then 3 new `finalize_failed` → fires again (re-armed); (d) `_drive_finalization_recovery`'s transient branch (outcome `failure_phase=='finalization'`, mode None) returns `'transient'` AND surfaces once.

Run:
```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_daemon.py -k "commit_worker_surface or transient_surface" -q
```
Expected: RED — no surface for the live worker; count not marker-aware; transient branch silent.

**Step 4 (GREEN — daemon):**
- Add a marker-aware failure counter. In `main.py`, add a helper that, given a worker's events, returns the number of `finalize_failed` events whose id is greater than the max id among that worker's `finalize_integrated`/`finalize_reopened` events (0 markers → count all failures). Add per-worker gate state `self._commit_failure_alerted: dict[str, int]` (init near `:1567`) mapping worker_id → the marker id (or 0) at which it last surfaced.
- In `check_workers` (`:4369`, at the TOP of the per-worker loop, before the marker/idle/dead branches): compute `events = self.registry.get_events_for_worker(worker_id)`; `since = count_failed_since_marker(events)`; `latest_marker = max_marker_id(events)`; if `since >= FINALIZE_DRIFT_RETRY_CAP` and `self._commit_failure_alerted.get(worker_id) != latest_marker`: set `self._commit_failure_alerted[worker_id] = latest_marker` and post the one-shot Slack+Brain surface (mention `reopen_for_edit`). On observing a `finalize_reopened` marker newer than any seen: `self._finalize_drift_retry.pop(worker_id, None)` and `self._finalize_recovery_alerted.discard(worker_id)` (fixes the stale-drift-state residual).
- Clear `self._commit_failure_alerted` in the non-running sweep (`:4093-4101`, alongside `_finalize_drift_retry`).
- Extend `_drive_finalization_recovery`'s transient branch (`:1715-1716`): when `_finalization_recovery_mode(outcome) is None` and `outcome` is a dict with `failure_phase=='finalization'` and `worker_id not in self._finalize_recovery_alerted`, add to `_finalize_recovery_alerted` and post the one-shot surface, then `return "transient"`.

**Step 5 (verify GREEN):**
```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_orchestrator_mcp.py tests/test_daemon.py -q
```
Expected: 0 failed.

**Step 6 (stage):**
```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/orchestrator_mcp.py commander/src/ironclaude/main.py commander/tests/test_orchestrator_mcp.py commander/tests/test_daemon.py
```

---

## Task 5: Component D — Brain give-up bound + 6d wizard terminal branch

**Files:**
- Modify: `commander/src/brain/rules/workflow.md`

**No tests required:** Brain-prompt rule change (behavioral; not unit-testable), consistent with prior Brain-rule loops.

**Step 1:** In SHIP checklist step 6 (~`:440`, the "call `commit_worker`" instruction), add: after 3 consecutive `commit_worker` failures with the same error for a worker, STOP calling `commit_worker` for it; pin a decision-format operator blocker (blocker/pin machinery ~`:894-938`); stop nudging /commit; offer `recover_worker_integration(worker_id, "reopen_for_edit")` as the sanctioned forward path (returns the worktree to editable `active` for re-staging — e.g. splitting an oversized file — snapshotting any uncommitted bytes; never discards reviewed work).

**Step 2:** In the "6d. Guided Integration-Recovery Wizard" (~`:566-601`), add a terminal branch: if `rerebase` / re-invoked `commit_worker` returns the same failure again, STOP the loop, pin an operator blocker, and offer `reopen_for_edit`; if the operator declines, the row stays frozen and preserved.

**Step 3 (verify markers landed — read-only):**
```bash
grep -n "reopen_for_edit" /Users/roberthyatt/Code/ironclaude/commander/src/brain/rules/workflow.md
```
Expected: matches in both the SHIP step 6 area and the 6d wizard area.

**Step 4 (stage):**
```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/src/brain/rules/workflow.md
```

---

## Task 6: Rebuild dist + full-suite verification

**Files:**
- Modify: `worker/mcp-servers/workspace-manager/dist/cli.js`
- Modify: `worker/mcp-servers/workspace-manager/dist/index.js`
- Modify: `worker/mcp-servers/workspace-manager/dist/hook-intent.js`

**Depends on:** Tasks 1, 2, 3, 4, 5.

**No tests required:** build + verification task (no new logic; runs the existing suites).

**Step 1 (rebuild dist):**
```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npm run build
```
Expected: `tsc && npm run bundle` completes with no error; esbuild writes `dist/cli.js`, `dist/index.js`, `dist/hook-intent.js`.

**Step 2 (confirm the new mode reached dist — read-only):**
```bash
grep -c "reopen_for_edit" /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/dist/cli.js
```
Expected: at least 1.

**Step 3 (full workspace-manager suite):**
```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npx vitest run
```
Expected: "passed | 0 failed" (onTaskUpdate RPC timeout benign).

**Step 4 (full commander suite):**
```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q
```
Expected: 0 failed.

**Step 5 (stage dist, force — tracked-but-gitignored):**
```bash
git -C /Users/roberthyatt/Code/ironclaude add -f worker/mcp-servers/workspace-manager/dist/cli.js worker/mcp-servers/workspace-manager/dist/index.js worker/mcp-servers/workspace-manager/dist/hook-intent.js
```

---

## Final verification (after all tasks)

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npx tsc --noEmit && npx vitest run
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q
```
Expected: both green (vitest judged by "0 failed").

## Notes

No version bump (operator decides at release). Commit/push operator-gated; no trailers. `dist/` staged with `git add -f`. The separate F1 test-only finding (vacuous heartbeat-test assertions) and the v1.1.11 review observations are a distinct follow-up loop, out of scope here.
