# Finalize-Recovery End-Review Corrective — Requirements

> **Created:** 2026-09-20
> **Status:** Operator-approved (end-review verdict HAS-ISSUES; operator: "Fold in the Boy-Scout isolation flake too"). Scope hold.

Corrective loop for the tier-up Fable END review of the maxBuffer+finalize-recovery effort. Fix the three verified MATERIAL findings + the operator-folded-in flaky-test fix. Design:
`docs/plans/2026-09-20-finalize-recovery-endreview-corrective-design.md`. Local tests only; no
version bump; commit/push operator-gated; no trailers. Invariant: never auto-abandon, never
discard work; the daemon surface only posts (never completes/kills).

## R1 (I1) — reopen re-arm fires once per new reopen marker
`commander/src/ironclaude/main.py` `check_workers` must re-arm drift/recovery state
(`_finalize_drift_retry.pop`, `_finalize_recovery_alerted.discard`) at most ONCE per new
`finalize_reopened` marker, not every cycle. Track the last-processed reopen marker id per
worker (`_commit_reopen_processed: dict[str,int]`); re-arm only when the latest reopen marker
id exceeds the stored one; clear the dict in the non-running sweep. Test in `test_daemon.py`:
a second `check_workers` cycle with the same newest reopen marker must NOT re-pop/re-discard; a
genuinely newer reopen marker re-arms once.

## R2 (I2) — reopen_for_edit preserves a diverged committed HEAD
`worker/mcp-servers/workspace-manager/src/integration.ts` `reopenForEdit` must never discard a
committed divergence above `frozen`: when the worktree is clean but `worktreeHead !== frozen`,
mint a recovery ref pointing at HEAD (mirroring `snapshotResidualIfDirty`'s content-addressed
create-only ref + `assignments.recovery_ref` + `preserved_work kind='recovery'` + re-verify
pattern) BEFORE `reset --hard frozen`, and return it as `recovery`. The clean-at-frozen case
keeps `recovery` undefined. Test in `integration-cases.ts`: a frozen-no-rebase row whose clean
HEAD is a commit above frozen → `reopen_for_edit` returns a `recovery.ref` resolving to that
HEAD, worktree lands at frozen, `preserved_work kind='recovery'` recorded.

## R3 (I3) — finalize_integrated logged on every integrate path that can leave finalize_failed rows uncovered
`commander/src/ironclaude/orchestrator_mcp.py` must log `finalize_integrated` wherever a worker's
finalization actually integrates so the daemon's since-marker count never fires a false
"reopen_for_edit" alert on an already-integrated worker: (a) `_classify_finalization_failure`
`state=="integrated"` branch; (b) `_drive_continue_recovery` integrated branch; (c) the daemon
integration seam `_finalize_and_release_worker` on its ACTUAL integrate result (the
`return {"action":"integrated",...}` success path — NOT the probe-first `elif state=="integrated"`
re-sighting branch, which is reached every idle cycle and is not an integrate event); and (d) the
daemon-driven drift path `drive_frozen_reconcile_recovery` when it reaches an integrated state
(mirroring its sibling `recover_worker_integration`). Tests: `test_orchestrator_mcp.py` for (a)/(b);
`test_worker_finalize_release.py` for (c) — positive (finalize success logs it) AND negative
(a probe re-sighting does NOT log it) — and (d).

## R4 (C4) — order-independent test mock on the threaded spawn_workers path
`commander/tests/test_orchestrator_mcp.py::test_batch_pm_failure_isolated_and_unregistered` must
use an argument-keyed (`session_name`) `_activate_pm_via_sqlite.side_effect`, not an ordered list,
because `spawn_workers` calls it concurrently via `ThreadPoolExecutor` (a scheduling race, not
request order). Add a comment noting the hazard. The fixed test passes deterministically.

## R5 — dist rebuild, full-suite verification, and cleanup
After the `integration.ts` change (R2), rebuild `worker/mcp-servers/workspace-manager/dist/`
(`npm run build`) and stage it (`git add -f`). Full vitest (0 failed) and full commander pytest
(0 failed, with a few repeats of the previously-flaky test to confirm stability). UNSTAGE the
stray `commander/docs/plans/2026-09-20-orchestrator-mcp-test-pollution*` docs the diagnosis
subagent staged (throwaway artifacts).

## R7 (C5) — regression fix: C1's re-arm guard must short-circuit
C1's landed `main.py` guard `if latest_reopen_id > self._commit_reopen_processed.get(...)` accesses
the attr unconditionally and AttributeErrors on `test_idle_worker_ttl.py`'s `__new__`-built
partial-daemon fixtures (7 `TestIdleGate` failures). Change it to
`if latest_reopen_id and latest_reopen_id > self._commit_reopen_processed.get(worker_id, 0):`
(short-circuits when no reopen marker; also clearer). Add `_commit_reopen_processed = {}` and
`_commit_failure_alerted = {}` to both `__new__` fixtures in `test_idle_worker_ttl.py`, and a
`test_daemon.py` test that a no-reopen-marker `check_workers` cycle runs the marker block without
error. Full commander pytest MUST return 0 failed after this (the `TestIdleGate` regression cleared).
C1-C4 already landed and are staged; this loop's remaining work is R7 + the dist rebuild / full-suite
verification (R5).

## R6 — Out of scope
The v1.1.11 F1 vacuous-heartbeat-test finding and the dead-`format_orphaned_unmerged`/CHANGELOG
observations (a separate v1.1.11-adjacent follow-up loop).
