# Finalize-Recovery End-Review Corrective + Test-Race Fix — Design

> **Created:** 2026-09-20
> **Status:** Design Complete
> **Type:** Corrective loop for the tier-up Fable END review of the maxBuffer+finalize-recovery effort (lineage 114, all tasks landed A), plus a Boy-Scout fix of a pre-existing flaky test the verification surfaced.
> **Scope mode:** hold (the 3 verified end-review findings I1/I2/I3 + the operator-folded-in C4 test-race fix).

## Summary

The Fable adversarial end review of the landed finalize-recovery diff returned HAS-ISSUES with three MATERIAL findings, all independently verified against current source. Plus, Task 6's full-suite verification surfaced one flaky commander test; the operator folded its fix into this loop under the Boy Scout Rule. All four are corrected here through TDD. Invariant (unchanged): never auto-abandon, never discard work; the daemon surface only posts, never completes/kills.

## Architecture

Four independent corrective changes across the same subsystems the effort touched — the daemon (`main.py`), the workspace-manager (`integration.ts`), the orchestrator (`orchestrator_mcp.py`), and a commander test (`test_orchestrator_mcp.py`) — plus a dist rebuild (C2 edits `integration.ts`) and a full-suite verification. No new subsystem, no behavior beyond closing the four defects.

## Components

### C1 — I1: reopen re-arm must fire once per new reopen marker (`commander/src/ironclaude/main.py`)

`check_workers` (`main.py:4446-4452`) re-arms drift/recovery state (`self._finalize_drift_retry.pop(worker_id)`, `self._finalize_recovery_alerted.discard(worker_id)`) whenever the highest-id marker is a `finalize_reopened` event — with **no record that this reopen was already processed** — so it fires **every cycle** while the newest marker stays a reopen (forever, for a worker that reopened then keeps failing without re-integrating). Consequences (verified against `_drive_finalization_recovery` `main.py:1637-1769`): the drift counter resets to 0 each cycle so `attempts > FINALIZE_DRIFT_RETRY_CAP` never holds (`held` unreachable, `drive_frozen_reconcile_recovery` re-driven every cycle); the conflict/repair one-shot and the new transient one-shot re-post every cycle (the dead-session/idle-TTL seams re-enter while the worker stays running).

**Fix:** add per-worker state `self._commit_reopen_processed: dict[str, int]` (init beside `_commit_failure_alerted` ~`:1599`); compute `latest_reopen_id` = max id among the worker's `finalize_reopened` events (0 if none); re-arm ONLY when `latest_reopen_id > self._commit_reopen_processed.get(worker_id, 0)`, then set `self._commit_reopen_processed[worker_id] = latest_reopen_id`. Clear it in the non-running sweep (`:4153-4158`) beside `_commit_failure_alerted`. Fires the re-arm exactly once per new reopen marker.

### C2 — I2: `reopen_for_edit` must preserve a diverged committed HEAD (`worker/mcp-servers/workspace-manager/src/integration.ts`)

`reopenForEdit` (`integration.ts:1936-1964`) guards only `classifyRebaseState === 'frozen-no-rebase'` (no rebase dir), NOT `worktreeHead === frozen`. When a `continue` rebase resolution completes and then fails the cumulative-effect equality proof, the worktree is left at the **resolved (diverged) committed HEAD**, clean, no rebase dir (`rebase-recovery-repair-required`) — exactly the state the daemon Slack text (`main.py:4458-4463`) and the 6d wizard (`workflow.md`) route operators to `reopen_for_edit` from. `snapshotResidualIfDirty` (`integration.ts:1472-1521`) returns `undefined` for a **clean** tree, so `reset --hard frozen` then discards those committed commits into reflog-only — violating never-discard. (A *dirty* diverged HEAD is already safe: `snapshotResidualIfDirty` commits residual with `-p HEAD`, so the diverged HEAD is reachable from the snapshot's parent.)

**Fix:** in `reopenForEdit`, after resolving `frozen` and before the reset, capture `const head = worktreeHead(worktree)`; call `snapshotResidualIfDirty(db, exact)` as today; if it returned `undefined` (clean) AND `head !== frozen`, mint a recovery ref pointing directly at `head`, mirroring `snapshotResidualIfDirty`'s exact pattern: content-addressed ref `refs/ironclaude/recovery/<guid>-<head>`, create-only `update-ref <ref> <head> ''` with tolerate-same-oid, `UPDATE assignments SET recovery_ref`, `insertPreservedWork(kind:'recovery', payload {ref, residualFiles:0})`, re-verify the ref resolves — then let the existing `reset --hard frozen` run, and return that ref as `recovery`. Net: a diverged committed HEAD is preserved and recoverable in every case; `head === frozen` + clean keeps `recovery` `undefined` as before.

### C3 — I3: log `finalize_integrated` at every commit_worker-reachable integrate point (`commander/src/ironclaude/orchestrator_mcp.py`)

`finalize_integrated` is logged only on `commit_worker`'s clean success path (`:3426`) and `recover_worker_integration`'s integrated branch. But a `commit_worker` that RAISES can still integrate via the probe/continue recovery inside `_classify_finalization_failure` — the `state == "integrated"` branch (`:3107-3115`) and `_drive_continue_recovery` reaching `_FINALIZATION_INTEGRATED_STATES` (`:3062-3067`) both return a dict with no `failure_phase`, so **no marker is logged**. The daemon's since-marker count then counts the earlier `finalize_failed` rows against no integrate marker -> a false "failing repeatedly, try reopen_for_edit" alert on an **already-integrated** row (and a Brain that follows it calls `reopen_for_edit` on an integrated row, routing into the integrated-cleanup/worktree-removal branch under a live worker).

**Fix:** `self.registry.log_event("finalize_integrated", worker_id=worker_id)` at each integrate point downstream of a `commit_worker` failure: (a) `_classify_finalization_failure` `state == "integrated"` branch (`:3107-3115`, before returning `status`); (b) `_drive_continue_recovery` integrated branch (`:3062-3067`, before `return integrated`). Also log it in the daemon integration seam `_finalize_and_release_worker` when it reaches an integrated state (verify the exact integrated check there and mirror). Idempotent-safe: a duplicate marker only advances the since-marker baseline harmlessly.

### C4 — Pre-existing flaky test: order-dependent mock on a threaded path (`commander/tests/test_orchestrator_mcp.py`)

`test_batch_pm_failure_isolated_and_unregistered` (`:6728-6743`) sets `tools._activate_pm_via_sqlite.side_effect = ["database busy", None]` — an ordered list. But `spawn_workers` runs `_start_batch_item` (which calls `_activate_pm_via_sqlite`) **concurrently** via `ThreadPoolExecutor` (`orchestrator_mcp.py:5657-5662`), so which thread consumes which list element is a scheduling race, not request order. Under full-suite load it occasionally flips -> the "good" worker consumes `"database busy"`, `result[0]` (the "bad" slot) holds a success dict with no `"error"` key -> `KeyError` at `:6739`. Proven by direct reproduction (forcing the "bad" thread to delay 50ms reproduces the exact KeyError in isolation; the argument-keyed fix passes 5/5 under the forced worst-case race). NOT cross-file pollution; two identical full-suite runs gave different results (3296 vs 1-failed).

**Fix:** replace the ordered list with an **argument-keyed** (order-independent) side_effect, matching the pattern the same class already uses for `_wait_for_ready` (`test_batch_dead_readiness_failure_isolated`):
```python
def _activate_side_effect(session_name, **kwargs):
    return "database busy" if session_name == "ic-bad" else None
tools._activate_pm_via_sqlite.side_effect = _activate_side_effect
```
Add a one-line comment noting the concurrency hazard (`spawn_workers` activates PM on worker threads; keep the side_effect argument-keyed, never an ordered list).

### C5 — Regression: C1's re-arm guard must short-circuit (`commander/src/ironclaude/main.py` + test fixtures)

C1's landed guard `if latest_reopen_id > self._commit_reopen_processed.get(worker_id, 0):` evaluates the right operand **unconditionally** (Python does not short-circuit a `>` comparison), so `check_workers` touches `self._commit_reopen_processed` on every running worker every cycle. `commander/tests/test_idle_worker_ttl.py` builds daemons via `IroncladeDaemon.__new__` (bypassing `__init__`, per its module docstring) and sets only `_finalize_drift_retry`/`_finalize_recovery_alerted`, NOT the newer `_commit_reopen_processed`/`_commit_failure_alerted` — so the guard raises `AttributeError: 'IroncladeDaemon' object has no attribute '_commit_reopen_processed'` (7 `TestIdleGate` failures). The pre-C1 code short-circuited (`if _latest_marker and any(...)`), so with empty events it never touched those attrs — which is why these `__new__` fixtures passed before.

**Fix (main.py):** change the guard to `if latest_reopen_id and latest_reopen_id > self._commit_reopen_processed.get(worker_id, 0):` — short-circuits when there is no reopen marker (also semantically clearer: no reopen ⇒ no re-arm), restoring the pre-C1 no-access-on-empty behavior. **Fix (test fixtures, robustness):** add `_commit_reopen_processed = {}` and `_commit_failure_alerted = {}` to both `IroncladeDaemon.__new__` fixtures in `test_idle_worker_ttl.py` so a future unconditional access does not silently break these tests again; add a `test_daemon.py` test that a partial/`__new__`-built daemon (or a worker with no reopen marker) runs the `check_workers` marker block without error. Not a behavior change to the daemon's operator-facing surface; it only fixes an attribute-access ordering bug and hardens the test fixtures.

## Data Flow

Unchanged from the landed effort; these fixes only correct: (C1) how often the daemon re-arms per reopen marker; (C2) what `reopen_for_edit` preserves before reset; (C3) which integrate paths emit the `finalize_integrated` marker the daemon counts against; (C4) a test's mock wiring; (C5) an attribute-access ordering bug in C1's guard that broke `__new__`-built test fixtures.

## Error Handling

C2 preserves work before any destructive reset (re-verifies the recovery ref resolves first, exactly as `snapshotResidualIfDirty` does). C1/C3 are additive bookkeeping (no new failure modes). C4 is test-only.

## Testing Strategy

TDD per code component.
- **C1:** extend `test_daemon.py` — after a reopen marker, a SECOND `check_workers` cycle (newest marker still that reopen, no new reopen) must NOT re-pop `_finalize_drift_retry` / re-discard `_finalize_recovery_alerted`; a genuinely NEW reopen marker (higher id) DOES re-arm once. RED against the current every-cycle behavior.
- **C2:** vitest `integration-cases.ts` — seed a frozen-no-rebase ready row whose worktree HEAD is a CLEAN commit **above** frozen; `reopen_for_edit` returns a `recovery.ref` resolving to that diverged HEAD, lands the worktree at frozen, records a `preserved_work kind='recovery'` row. RED against the current unconditional discard.
- **C3:** `test_orchestrator_mcp.py` — a `commit_worker` whose finalize raises but whose probe classifies `state=="integrated"` (and the `_drive_continue_recovery` integrated case) logs `finalize_integrated`. RED against the current no-marker behavior.
- **C4:** the fixed test is order-independent; verify it passes (falsifiability is structural — an argument-keyed side_effect cannot be consumed out of order). Keep the existing behavioral assertions.
- **dist + suites:** rebuild `dist/` (C2 changed `integration.ts`); full vitest (0 failed) + full commander pytest (0 failed, with a few repeats of the previously-flaky test to confirm stability).

## Implementation Notes

Local tests only. No version bump. Commit/push operator-gated; no trailers. `dist/` staged with `git add -f`. The stray `commander/docs/plans/2026-09-20-orchestrator-mcp-test-pollution*` docs (staged by the diagnosis subagent's own workflow loop) must be UNSTAGED as part of this loop — throwaway investigation artifacts, not release content. Out of scope: the v1.1.11 F1 vacuous-heartbeat-test finding and the dead-`format_orphaned_unmerged`/CHANGELOG observations (a separate v1.1.11-adjacent follow-up). New lineage; earns its own blind plan review. LESSON: do NOT dispatch a Bash-needing subagent during a gated (non-executing) workflow stage — it drives the shared session state machine to unblock itself; run such investigation in the executing stage.
