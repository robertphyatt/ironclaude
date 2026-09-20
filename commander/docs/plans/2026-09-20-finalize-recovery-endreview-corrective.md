# Finalize-Recovery End-Review Corrective — Completion Plan (post-retreat)

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Fix the C5 regression (Task-1's re-arm guard AttributeErrors on `__new__`-built daemon test fixtures) that the full-suite verification caught, then rebuild dist and verify both suites green.

**Requirements:** docs/plans/2026-09-20-finalize-recovery-endreview-corrective-requirements.md

**Design:** docs/plans/2026-09-20-finalize-recovery-endreview-corrective-design.md

**Architecture:** C1-C4 (I1/I2/I3/C4) already landed and are staged; this post-retreat completion plan finishes the loop — a one-line short-circuit in `main.py` + test-fixture hardening (C5), then the dist rebuild + full-suite verification that was interrupted.

**Tech Stack:** Python (commander, pytest), TypeScript (workspace-manager, vitest).

**Execution invariants:** Shell state does NOT persist between steps — literal absolute paths. Bash cwd is `commander/`; use `git -C /Users/roberthyatt/Code/ironclaude`. `docs/` gitignored (`git add -f`). `PYTHONUNBUFFERED=1` on pytest. vitest may emit the benign `onTaskUpdate` RPC timeout — judge by "N passed | 0 failed". No version bump; commit/push operator-gated; no trailers.

---

## Task 1: C5 — short-circuit the reopen re-arm guard + harden the `__new__` test fixtures

**Files:**
- Modify: `commander/src/ironclaude/main.py`
- Modify: `commander/tests/test_idle_worker_ttl.py`
- Modify: `commander/tests/test_daemon.py`

**Step 1 (RED — confirm the regression + add a FALSIFIABLE partial-daemon guard test):** The 7 `TestIdleGate` failures in `test_idle_worker_ttl.py` are the RED (run them to confirm `AttributeError: 'IroncladeDaemon' object has no attribute '_commit_reopen_processed'`). Additionally, in `test_daemon.py` add `test_check_workers_marker_block_no_reopen_marker_no_attr_error` that is genuinely RED before the fix: use `_live_worker(daemon)`, then make the daemon a partial one that LACKS the new attrs — `del daemon._commit_reopen_processed` and `del daemon._commit_failure_alerted` (they exist because the `daemon` fixture runs real `__init__`; delete them to model the `__new__` path). Set `daemon.registry.get_events_for_worker.return_value` to TWO `finalize_failed` events and NO reopen/integrated marker (so `_since < FINALIZE_DRIFT_RETRY_CAP` → the `_commit_failure_alerted` surface short-circuits and is not the thing under test). Pre-seed `daemon._finalize_drift_retry["w1"] = 2` and `daemon._finalize_recovery_alerted.add("w1")`. Call `daemon.check_workers()` and assert it does NOT raise AND `daemon._finalize_drift_retry.get("w1") == 2` and `"w1" in daemon._finalize_recovery_alerted` (no re-arm, no attr access). This is RED before Step 2 (removing/without the `latest_reopen_id and` short-circuit → `AttributeError` on the deleted `_commit_reopen_processed`) and GREEN after.

Run:
```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_idle_worker_ttl.py -q
```
Expected: RED — 7 `TestIdleGate` failures with `AttributeError ... _commit_reopen_processed`.

**Step 2 (GREEN — main.py short-circuit):** In `main.py` `check_workers`, change the re-arm guard from:
```python
            if latest_reopen_id > self._commit_reopen_processed.get(worker_id, 0):
```
to:
```python
            if latest_reopen_id and latest_reopen_id > self._commit_reopen_processed.get(worker_id, 0):
```
(When there is no reopen marker, `latest_reopen_id` is 0 and the `and` short-circuits before touching `_commit_reopen_processed` — restoring the pre-C1 no-access-on-empty behavior and expressing "no reopen ⇒ no re-arm" directly.)

**Step 3 (GREEN — fixture hardening):** In `test_idle_worker_ttl.py`, in BOTH `IroncladeDaemon.__new__(IroncladeDaemon)` fixtures (the two blocks that set `daemon._finalize_drift_retry = {}` / `daemon._finalize_recovery_alerted = set()`), add `daemon._commit_reopen_processed = {}` and `daemon._commit_failure_alerted = {}` so a future unconditional access does not silently break these tests.

**Step 4 (verify):**
```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_idle_worker_ttl.py tests/test_daemon.py -q
```
Expected: 0 failed.

**Step 5 (stage):**
```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/main.py commander/tests/test_idle_worker_ttl.py commander/tests/test_daemon.py
```

---

## Task 2: Rebuild dist + full-suite verification + cleanup

**Files:**
- Modify: `worker/mcp-servers/workspace-manager/dist/cli.js`
- Modify: `worker/mcp-servers/workspace-manager/dist/index.js`
- Modify: `worker/mcp-servers/workspace-manager/dist/hook-intent.js`

**Depends on:** Task 1.

**No tests required:** build + verification + cleanup task.

**Step 1 (rebuild dist — the corrective loop changed integration.ts in C2):**
```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npm run build
```
Expected: `tsc && bundle` no error.

**Step 2 (full workspace-manager vitest):**
```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npx vitest run
```
Expected: "passed | 0 failed".

**Step 3 (full commander pytest — the C5 regression must be cleared):**
```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q
```
Expected: 0 failed (all TestIdleGate green; the previously-flaky test_batch_pm_failure stable).

**Step 4 (unstage the stray diagnosis docs — not a file edit):**
```bash
git -C /Users/roberthyatt/Code/ironclaude restore --staged commander/docs/plans/2026-09-20-orchestrator-mcp-test-pollution-design.md commander/docs/plans/2026-09-20-orchestrator-mcp-test-pollution.md commander/docs/plans/2026-09-20-orchestrator-mcp-test-pollution.plan.json
```
Expected: those 3 paths no longer staged.

**Step 5 (stage dist, force):**
```bash
git -C /Users/roberthyatt/Code/ironclaude add -f worker/mcp-servers/workspace-manager/dist/cli.js worker/mcp-servers/workspace-manager/dist/index.js worker/mcp-servers/workspace-manager/dist/hook-intent.js
```

---

## Final verification

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npx tsc --noEmit && npx vitest run
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q
```
Expected: both green.

## Notes

Post-retreat completion of lineage 116 (C1-C4 landed+staged; this plan finishes C5 + verification). No version bump; commit/push operator-gated; no trailers. `dist/` staged with `git add -f`.
