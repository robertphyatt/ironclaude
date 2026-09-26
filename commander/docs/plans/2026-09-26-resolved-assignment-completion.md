# Resolved-Assignment Completion Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Have the finalization status probe report `resolved` for a `cleaned` or
`abandoned` assignment. When it does, the terminal seam completes a dead worker instead
of retrying a refused abandon forever.

**Requirements:** docs/plans/2026-09-26-resolved-assignment-completion-requirements.md

**Design:** docs/plans/2026-09-26-resolved-assignment-completion-design.md

**Architecture:**
- **Workspace-manager:** `reconcileFinalization`'s non-mutating `status` branch gains a
  `resolved` state for terminal lifecycles other than `integrated`.
- **Seam router:** the Commander seam's probe-first router gets a `resolved` branch that
  mirrors the existing `integrated` branch. It calls `_complete_worker_if_session_dead`
  and never finalizes or abandons.

**Tech Stack:** TypeScript + vitest (workspace-manager), Python + pytest (commander).

## Grounding (verified against live source while planning)

- `worker/mcp-servers/workspace-manager/src/integration.ts`:
  - the `FinalizationResult.state` union is at :45-50;
  - the `status` branch is at :2061-2070 (`integrated` returns `integrated`,
    `ready_for_integration` returns its rebase-state classification, everything else
    returns `{ state: 'not-ready' }` at :2069);
  - `exactAssignment` (:471-485) filters only on repository and owner, not lifecycle, so
    a `cleaned` or `abandoned` row reaches the status branch.
- `workspace-service.ts:674-679`: `abandonWorkspace` returns an `abandoned` row as-is and
  throws `'Only unresolved managed worktrees can be abandoned'` for any other terminal
  lifecycle. `nonterminal` (:254-256) excludes `integrated`, `abandoned` and `cleaned`.
- `db.ts:99`: the `lifecycle_status` CHECK allows `cleaned` and `abandoned`. The tests
  already set lifecycle with a direct `UPDATE`, as `integration-cases.ts:1879` does.
- `src/__tests__/integration-cases.ts`:
  - `setup(withRemote)` (:65-87) returns `{ root, database, assignment }` for an active
    row;
  - the status-probe tests sit inside `if (part === 'recovery')` (from :2254), which
    `integration-recovery.test.ts` runs;
  - the active-row test `'reconcile status on an active (not-ready) row returns not-ready
    without throwing'` is at :2702-2709.
- `commander/src/ironclaude/orchestrator_mcp.py`:
  - in the probe-first router, `if state == "not-ready":` is at :3686 and the
    `elif state == "integrated":` branch is at :3688-3695 (it calls
    `self._complete_worker_if_session_dead(worker_id, ssh_host)`, then
    `_trigger_integrated_cleanup`, then `return status`);
  - an unrecognized state falls to the `else` branch at :3716-3726, which returns
    `failure_phase: "probe"`;
  - the terminal request is downgraded when the session is alive (:3653-3659);
  - `_complete_worker_if_session_dead` is at :2975-2996.
- `commander/tests/test_worker_finalize_release.py`, `TestProbeFirstRouter`:
  - `test_probe_integrated_live_session_never_completes_but_still_cleans` ends at :904;
    the next test starts at :906;
  - `_make_tools(worker, *, gates_pass=True, has_session=False)` and
    `_make_repo(base, *, new_work)` are the fixtures.
- `CHANGELOG.md` has no `## [Unreleased]` heading at present (it was renamed at the
  v1.1.12 release). Line 13 is `## 1.1.12: …`.

## Execution invariants

- Bash cwd is `/Users/roberthyatt/Code/ironclaude/commander`. Every command uses absolute
  paths or `git -C /Users/roberthyatt/Code/ironclaude`.
- Shell state does not persist between steps.
- Run pytest as `PYTHONUNBUFFERED=1 .venv/bin/python -m pytest`.
- `docs/` and `dist/` need `git add -f`.

---

## Task 1: `resolved` status-probe state (workspace-manager)

**Files:**
- Modify: `worker/mcp-servers/workspace-manager/src/integration.ts` (:45-50, :2061-2070)
- Test: `worker/mcp-servers/workspace-manager/src/__tests__/integration-cases.ts`

**Step 1 (RED): Add the tests.** In `integration-cases.ts`, insert this directly after the
closing `});` of `it('reconcile status on an active (not-ready) row returns not-ready
without throwing', …)` (:2709):

```ts
  for (const lifecycle of ['cleaned', 'abandoned'] as const) {
    it(`reconcile status on a ${lifecycle} row returns resolved (nothing left to finalize or rescue)`, () => {
      const { root, database, assignment } = setup(false);
      database.prepare('UPDATE assignments SET lifecycle_status = ? WHERE workspace_guid = ?')
        .run(lifecycle, assignment.workspace_guid);
      const status = reconcileFinalization(database, {
        repositoryPath: root, workspaceGuid: assignment.workspace_guid, providerRootSessionId: OWNER,
        rebaseRecovery: 'status',
      });
      expect(status.state).toBe('resolved');
      expect(status.detail).toContain(lifecycle);
    });
  }
```

**Step 2: Run the tests and confirm RED.**

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npx vitest run src/__tests__/integration-recovery.test.ts
```

Expected: exactly 2 failed, the two new tests (received `'not-ready'`, expected
`'resolved'`). Every other test passes.

**Step 3 (GREEN): Implement in `integration.ts`.**

(a) In the `FinalizationResult.state` union (:49), change
`    | 'integrated' | 'not-ready' | 'reconciled' | 'committed' | 'closed-out'` to
`    | 'integrated' | 'not-ready' | 'resolved' | 'reconciled' | 'committed' | 'closed-out'`.

(b) In the `status` branch, replace `    return { state: 'not-ready' };` (:2069, the line
directly after the `ready_for_integration` block and before the branch's closing `}`)
with:

```ts
    // A cleaned row's work already landed or was preserved; an abandoned row
    // was already abandoned (abandon returns it as a no-op). Either way nothing
    // is left to finalize or rescue. Report it distinctly so the terminal seam
    // completes a dead worker instead of re-attempting an abandon that refuses
    // a cleaned (or integrated) row. 'not-ready' now means strictly an active row.
    if (assignment.lifecycle_status === 'cleaned' || assignment.lifecycle_status === 'abandoned') {
      return {
        state: 'resolved',
        detail: `Assignment lifecycle is ${assignment.lifecycle_status}; nothing left to finalize or rescue.`,
      };
    }
    return { state: 'not-ready' };
```

**Step 4: Run the tests and confirm GREEN.**

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npx vitest run src/__tests__/integration-recovery.test.ts src/__tests__/integration-core.test.ts
```

Expected: 0 failed, including the existing active-row `not-ready` test.

**Step 5: Stage the changes.**

```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/mcp-servers/workspace-manager/src/integration.ts worker/mcp-servers/workspace-manager/src/__tests__/integration-cases.ts
```

Expected: both files are staged.

---

## Task 2: Seam router `resolved` branch (commander)

**Files:**
- Modify: `commander/src/ironclaude/orchestrator_mcp.py` (router, after :3695)
- Test: `commander/tests/test_worker_finalize_release.py` (`TestProbeFirstRouter`)

**Step 1 (RED): Add the tests.** Insert these two methods into `TestProbeFirstRouter`
directly after `test_probe_integrated_live_session_never_completes_but_still_cleans`
(which ends at :904), before `def test_probe_rebase_paused_clean_live_session_never_completes`:

```python
    def test_probe_resolved_dead_session_completes_never_finalizes_or_abandons(
        self, tmp_path,
    ):
        # The assignment is already cleaned/abandoned (work landed or preserved):
        # nothing left to rescue. A dead worker is completed by the seam; finalize
        # and abandon (which refuses a terminal lifecycle) are never reached.
        repo = _make_repo(tmp_path / "wt", new_work=True)
        tools = _make_tools(_worker(repo))
        tools._workspace_client.reconcile.return_value = {
            "state": "resolved",
            "detail": "Assignment lifecycle is cleaned; nothing left to finalize or rescue.",
        }
        out = tools._finalize_and_release_worker("w1", "session ended", terminal=True)
        assert out["state"] == "resolved"
        tools.registry.update_worker_status.assert_called_once_with("w1", "completed")
        tools._workspace_client.finalize.assert_not_called()
        tools._workspace_client.abandon.assert_not_called()
        assert tools._workspace_client.reconcile.call_count == 1

    def test_probe_resolved_live_session_never_completes(self, tmp_path):
        # SAFETY GATE: a resolved assignment but a STILL-ALIVE session is never
        # completed; nothing is finalized or abandoned.
        repo = _make_repo(tmp_path / "wt", new_work=True)
        tools = _make_tools(_worker(repo), has_session=True)
        tools._workspace_client.reconcile.return_value = {
            "state": "resolved",
            "detail": "Assignment lifecycle is abandoned; nothing left to finalize or rescue.",
        }
        out = tools._finalize_and_release_worker("w1", "session ended", terminal=True)
        assert out["state"] == "resolved"
        tools.registry.update_worker_status.assert_not_called()
        tools._workspace_client.finalize.assert_not_called()
        tools._workspace_client.abandon.assert_not_called()
```

**Step 2: Run the tests and confirm RED.**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_worker_finalize_release.py -k "probe_resolved"
```

Expected: 2 failed. Today `resolved` falls to the router's unknown-state branch, which
returns `{"failure_phase": "probe", ...}` with no `state` key, so `out["state"]` raises
`KeyError`.

**Step 3 (GREEN): Implement in `orchestrator_mcp.py`.** In the probe-first router, insert
this branch directly after the `integrated` branch's `return status` (:3695) and before
`elif state == "rebase-paused-clean":`:

```python
        elif state == "resolved":
            # The assignment is already cleaned/abandoned: its work was
            # integrated or preserved to a recovery ref, so nothing is left to
            # finalize or rescue. Complete the worker (only when its session is
            # confirmed dead) — NEVER finalize or abandon a resolved row;
            # abandon refuses every terminal lifecycle, which left dead workers
            # re-running a failing seam forever.
            self._complete_worker_if_session_dead(worker_id, ssh_host)
            return status
```

**Step 4: Run the tests and confirm GREEN.**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_worker_finalize_release.py
```

Expected: 0 failed.

**Step 5: Stage the changes.**

```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/orchestrator_mcp.py commander/tests/test_worker_finalize_release.py
```

Expected: both files are staged.

---

## Task 3: dist rebuild, full suites, CHANGELOG `[Unreleased]` entry

No tests are required for this task: it is a build, a documentation entry, and full-suite
verification. The behavior is tested in Tasks 1 and 2.

**Files:**
- Modify: `worker/mcp-servers/workspace-manager/dist/cli.js`, `dist/index.js`, `dist/hook-intent.js` (build output)
- Modify: `CHANGELOG.md`

**Step 1: Rebuild.**

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npm run build
```

Expected: `tsc` and the bundle both succeed, with no error.

**Step 2: Confirm the `resolved` state is in both bundles.** esbuild re-emits string
literals with double quotes: `return { state: 'not-ready' };` appears in `dist/cli.js` as
`return { state: "not-ready" };`. So the guard searches the double-quoted form. While
planning, it had 0 matches in each file.

```bash
rg -n -F 'state: "resolved"' /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/dist/cli.js /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/dist/index.js
```

Expected: exactly one match in `dist/cli.js` and exactly one in `dist/index.js`.

**Step 3: Run the full workspace-manager suite.**

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npx vitest run
```

Expected: 0 failed. Ignore the benign `onTaskUpdate` RPC-timeout error, which is
pre-existing.

**Step 4: Run the full commander suite.**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q
```

Expected: 0 failed.

**Step 5: Add the CHANGELOG entry.** Insert this block immediately before the line
`## 1.1.12: Proactive per-orphan discussion, default-branch orphan reaping, visible
terminal-finalize failures, and a naturally-resolving Opus tier`, followed by one blank
line:

```markdown
## [Unreleased]

- **A dead worker whose workspace assignment is already resolved is now completed instead of retried forever.** The v1.1.12 WARNING log showed why the worker the v1.1.12 finalize-visibility entry describes (left `running` ~13 days) was never completed: its assignment was already `cleaned` (work preserved earlier), but the finalization status probe reported every non-integrated terminal lifecycle as `not-ready`, so the terminal seam fell through to `abandon`, which no-ops an `abandoned` row but refuses a `cleaned` or `integrated` one (`Only unresolved managed worktrees can be abandoned`) — every daemon tick. The status probe now reports `resolved` for a `cleaned` or `abandoned` assignment (`not-ready` again means strictly an active row), and the seam's probe-first router completes the worker on `resolved` — only when its tmux session is confirmed dead — never finalizing or abandoning it. A live session is never completed, and the seam still owns all completion. (`integration.ts`, `orchestrator_mcp.py`, rebuilt `dist/`; covered by `integration-cases.ts` / `test_worker_finalize_release.py`.) Deploy: refresh the plugin-cache workspace-manager `dist/`, then restart Commander.
```

**Step 6: Stage the changes.**

```bash
git -C /Users/roberthyatt/Code/ironclaude add -f worker/mcp-servers/workspace-manager/dist/cli.js worker/mcp-servers/workspace-manager/dist/index.js worker/mcp-servers/workspace-manager/dist/hook-intent.js CHANGELOG.md commander/docs/plans/2026-09-26-resolved-assignment-completion.md commander/docs/plans/2026-09-26-resolved-assignment-completion.plan.json
```

Expected: all files are staged.
