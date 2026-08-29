# M1 — Never-Lose-Work Completeness Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Close the tombstone teardown's push-pending gap (guard + reaper recognition) and make the Commander finalize lane graceful, shipping as v1.1.8.

**Requirements:** docs/plans/2026-08-24-m1-never-lose-work-completeness-requirements.md

**Design:** docs/plans/2026-08-24-m1-never-lose-work-completeness-design.md

**Architecture:** Approach B. A shared predicate exported from `integration.ts` backs a fail-closed guard in `tombstoneTerminalAssignment` (the third teardown primitive) so no caller discards a push-pending obligation. The daemon reaper recognizes a push-pending row (`_is_protected`), preserves it, and emits a one-time per-guid WARNING (surface once, no per-cycle spam). The Commander finalize lane guards inline for graceful `integrated-local`.

**Tech Stack:** TypeScript (workspace-manager, vitest), Python (commander, pytest via `.venv`).

**Execution invariants (blind-reviewer contract):** Shell state does not persist between steps; absolute paths. Execution cwd is `commander/`; vitest steps `cd` to the workspace-manager package absolutely; pytest uses `commander/.venv/bin/python`. `docs/` is gitignored (`git add -f`). Every new test asserts a value that flips when its guard is deleted. `integration-cases.ts` is a helper run via `integration-core.test.ts` (target THAT for `-t` runs). The TS↔Python phase set (`push-pending`/`push-succeeded`/`push-failed`) must stay identical.

---

## Task 1: Export predicate + tombstone guard + TS test

**Files:**
- Modify: `worker/mcp-servers/workspace-manager/src/integration.ts` (export a predicate near `decodePushDisposition` ~:156)
- Modify: `worker/mcp-servers/workspace-manager/src/workspace-service.ts` (`tombstoneTerminalAssignment` ~:698-740)
- Test: `worker/mcp-servers/workspace-manager/src/__tests__/workspace-service.test.ts`

**Step 1: Write the RED test.** Model on the existing tombstone test (`:1135`) and integrated-row cleanup tests (`:683`/`:755`). Build an `integrated` assignment carrying a **fully-valid** push-pending disposition (all of `candidateCommit`/`frozenCommit`/`remoteName`/`remoteUrl`/`destinationRef`, else `decodePushDisposition` returns undefined and the guard never fires — model the JSON on `seedIntegratedPushPending` in `integration-cases.ts:146-169`), then call `cleanupWorkspace`; it must throw and preserve. Construct the integrated push-pending row from this file's own primitives (`initDb`, `recordIntegration`, `WorkspaceService` + a direct `database.prepare("UPDATE assignments SET lifecycle_status='integrated', integrated_commit=?, current_head=?, disposition=?")`). Assert:
```ts
expect(() => manager.cleanupWorkspace({ repositoryPath: root, workspaceGuid: guid, ownerSessionId: OWNER }))
  .toThrow('push-pending obligation');
expect(existsSync(assignment.worktree_path)).toBe(true);
expect(database.prepare('SELECT lifecycle_status, disposition FROM assignments WHERE workspace_guid = ?').get(guid))
  .toMatchObject({ lifecycle_status: 'integrated', disposition: expect.stringContaining('push-pending') });
expect(git(root, 'branch', '--list', assignment.branch)).not.toBe('');
```

**Step 2: Run the RED test — verify FAIL.**
```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npx vitest run src/__tests__/workspace-service.test.ts -t "push-pending obligation"
```
Expected: FAIL — `cleanupWorkspace` returns `cleaned` (no throw); worktree/branch removed.

**Step 3: Export the predicate.** In `integration.ts`, immediately after `decodePushDisposition` (~:169), add:
```ts
export function hasPushPendingObligation(disposition: string | null): boolean {
  return decodePushDisposition(disposition) !== undefined;
}
```

**Step 4: Add the tombstone guard.** In `workspace-service.ts`, import the predicate from `../integration.js` (match the existing import style) and, inside `tombstoneTerminalAssignment`'s integrated `else` branch, right after the integrated-evidence proof (`:721-728` throw) and before the branch closes (`:729`), add:
```ts
      if (hasPushPendingObligation(assignment.disposition)) {
        throw new Error('Refusing to tombstone a worktree with a push-pending obligation; resolve or push it first');
      }
```
(Only integrated rows reach this branch; push-pending rides only integrated rows. Both callers pass a `SELECT *` row, so `assignment.disposition` is populated.)

**Step 5: Run the test — verify GREEN.**
```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npx vitest run src/__tests__/workspace-service.test.ts -t "push-pending obligation"
```
Expected: PASS (throws; worktree/branch/row/disposition preserved).

**Step 6: Full workspace-manager suite — no regression.**
```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npx vitest run
```
Expected: all pass.

**Step 7: Stage.**
```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/mcp-servers/workspace-manager/src/integration.ts worker/mcp-servers/workspace-manager/src/workspace-service.ts worker/mcp-servers/workspace-manager/src/__tests__/workspace-service.test.ts
```

---

## Task 2: I-1 Commander graceful handling + test

**Files:**
- Modify: `worker/mcp-servers/workspace-manager/src/integration.ts` (`finalizeCommanderLocalCommit` ~:1194-1207)
- Test: `worker/mcp-servers/workspace-manager/src/__tests__/integration-cases.ts`

**Depends on:** Task 1.

**Step 1: Write the RED test** in the core region of `integration-cases.ts` (near the `finalizeCommanderLocalCommit` tests ~:474/:492). Seed an active row with an `integration-pending` disposition (so `markIntegrated` converts it to `push-pending`; mirror the v1.1.7 ACTIVE `/commit` seed), then call `finalizeCommanderLocalCommit`. Assert `state === 'integrated-local'`, the row is `integrated` + `push-pending` preserved, worktree present. Name it so a `-t "Commander"` filter matches.

**Step 2: Run RED — verify FAIL.**
```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npx vitest run src/__tests__/integration-core.test.ts -t "Commander"
```
Expected: FAIL — reaches `disposeFinalized` → `recycleFinalized` → v1.1.7 backstop throws.

**Step 3: Add the inline guards.** In `finalizeCommanderLocalCommit`, before EACH `disposeFinalized` call (~:1198 repair branch, ~:1204 main branch), insert:
```ts
    if (decodePushDisposition(local.assignment.disposition)) {
      return { state: 'integrated-local', integratedCommit: <candidate|local.integratedCommit>, pushError: 'Remote has not proved the exact integrated candidate' };
    }
```
(Use `candidate` in the repair branch, `local.integratedCommit` in the main branch — match each existing `return { state: 'cleaned', integratedCommit: … }` at :1199/:1205.) Do NOT replace `disposeFinalized` — it honors `input.dispose` (`'release'` teardown).

**Step 4: Run the test — verify GREEN.**
```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npx vitest run src/__tests__/integration-core.test.ts -t "Commander"
```
Expected: PASS (`integrated-local`, preserved).

**Step 5: Full suite — no regression** (existing `finalizeCommanderLocalCommit` `'cleaned'` tests carry no disposition).
```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npx vitest run
```
Expected: all pass.

**Step 6: Stage.**
```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/mcp-servers/workspace-manager/src/integration.ts worker/mcp-servers/workspace-manager/src/__tests__/integration-cases.ts
```

---

## Task 3: Reaper recognition (protect + one-time WARNING) + Python test

**Files:**
- Modify: `commander/src/ironclaude/main.py`
- Test: `commander/tests/test_worktree_reaper.py`

**Depends on:** Task 1 (phase-set consistency).

**Step 1: Write the RED tests** in `test_worktree_reaper.py` (schema already has `disposition TEXT` at `:99`; `json` is imported). Add a module constant `_PP_DISPOSITION = json.dumps({"phase":"push-pending","candidateCommit":"abc123","frozenCommit":"def456","remoteName":"origin","remoteUrl":"https://example.invalid/r.git","destinationRef":"refs/heads/main","expectedRemoteOldOid":"000"})`. Then:
- `_has_push_pending` unit cases: True for phases `push-pending`/`push-succeeded`/`push-failed`; False for `None`, `""`, `"{}"`, `'{"phase":"integration-pending"}'`, `"not-json"`.
- In `TestIsProtected` (~:576): `test_push_pending_disposition_protects` — `_is_protected({"workspace_guid":_W1,"repository_identity":"repo-id","updated_at":None,"disposition":_PP_DISPOSITION}, None, tmux_dead, set(), now=0.0) is True`.
- Sweep test `test_push_pending_row_is_preserved_and_warned_exactly_once` (model on the sweep tests ~:557/:569): `_insert_worker(commander,"w1",status="completed",finished_ago="25 hours",workspace_guid=_W1)`; `_insert_assignment(ws_conn,_W1,owner_session_id=_OWNER,lifecycle_status="integrated",updated_ago="25 hours",disposition=_PP_DISPOSITION)`; a test-local `alerted: set[str] = set()`; call `_reap_leaked_worktrees(commander, client, tmux, workspace_db_path=str(ws), push_pending_alerted=alerted)` twice under `caplog.at_level("WARNING")` (clear caplog between); assert the "pending push" WARNING appears exactly once on the first call and zero on the second (use `r.getMessage()`), `client.cleanup/abandon/reap` not called, `counts["push_pending"] == 1` both calls.

Also extend the helper `_insert_assignment` (~:136-148): add a `disposition=None` kwarg, add the `disposition` column + a `?` placeholder to its INSERT (all 17 existing callers keep working via the default).

**Step 2: Run the RED tests — verify FAIL.**
```bash
cd /Users/roberthyatt/Code/ironclaude/commander && .venv/bin/python -m pytest tests/test_worktree_reaper.py -q -k "push_pending"
```
Expected: FAIL — `_has_push_pending` undefined; `_is_protected` returns False (not protected); the sweep calls `client.cleanup` and `counts` has no `push_pending` key.

**Step 3: Add `_has_push_pending`** near the reaper helpers in `main.py`:
```python
def _has_push_pending(disposition_json: str | None) -> bool:
    """True when the row still owes a push (mirrors TS decodePushDisposition's phase set)."""
    if not disposition_json:
        return False
    try:
        phase = json.loads(disposition_json).get("phase")
    except (ValueError, TypeError):
        return False
    return phase in ("push-pending", "push-succeeded", "push-failed")
```

**Step 4: Recognize push-pending in `_is_protected`** (`:288-311`), inside the `try`, after the lock check (`:303-304`) before `return False`:
```python
        if _has_push_pending(assignment.get("disposition")):
            return True
```

**Step 5: Thread the caller-owned alerted set + one-time WARNING.**
- `_reap_leaked_worktrees` signature (~:408-416): add keyword-only `push_pending_alerted: set[str] | None = None`.
- counts init (`:427`): add `"push_pending": 0`; immediately after, add `if push_pending_alerted is None:\n        push_pending_alerted = set()`.
- Replace the protected branch (`:479-483`) with:
```python
        if _is_protected(assignment, worker, tmux, locked_workspace_guids, now):
            if _has_push_pending(assignment.get("disposition")):
                counts["push_pending"] += 1
                if guid not in push_pending_alerted:
                    logger.warning(
                        "Worktree reaper: workspace=%s repo=%s is integrated with a pending "
                        "push — preserved, not reaped; complete the push (e.g. /push) to release it",
                        guid, assignment.get("repository_identity"),
                    )
                    push_pending_alerted.add(guid)
            else:
                counts["protected"] += 1
            continue
```
- Daemon `__init__` (next to `self._message_aging_alerted` ~:1254): `self._push_pending_alerted: set[str] = set()`.
- Caller (`:1619-1622`): add `push_pending_alerted=self._push_pending_alerted,`. Leave the `:1623` summary-log gate UNCHANGED — the sweep WARNING is the R3 surface.

**Step 6: Update the two exact-dict count assertions.** In `test_worktree_reaper.py`, the assertions at ~:201 and ~:303 (`assert counts == {"released": …, "surfaced": 0, "protected": 0, "errors": 0}`) MUST gain `"push_pending": 0` to match the new init. Read each and add the key.

**Step 7: Run the tests — verify GREEN.**
```bash
cd /Users/roberthyatt/Code/ironclaude/commander && .venv/bin/python -m pytest tests/test_worktree_reaper.py -q
```
Expected: all pass (push-pending row preserved, warned once; existing tests updated for the new count key).

**Step 8: Stage.**
```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/main.py commander/tests/test_worktree_reaper.py
```

---

## Task 4: Release v1.1.8

**Files:**
- Modify: `commander/pyproject.toml`, `worker/.claude-plugin/plugin.json`, `worker/.codex-plugin/plugin.json`, `worker/mcp-servers/workspace-manager/package.json`, `.claude-plugin/marketplace.json`, `CHANGELOG.md`, `README.md`

**Depends on:** Task 2, Task 3.

**No tests required for the version edits** (config); `test_version_consistency.py` verifies. CHANGELOG/README are docs.

**Step 1: Bump the five version sources from `1.1.7` to `1.1.8`** (codex keeps `1.1.8+codex.<fresh YYYYMMDDHHMMSS>`; marketplace is `plugins[0].version`).

**Step 2: Verify version consistency.**
```bash
cd /Users/roberthyatt/Code/ironclaude/commander && .venv/bin/python -m pytest tests/test_version_consistency.py -q
```
Expected: passes.

**Step 3: Add a `## 1.1.8: …` CHANGELOG.md section** above `## 1.1.7`: the tombstone push-pending guard (third teardown primitive now covered), reaper protect + one-time WARNING (no per-cycle spam, no silence), Commander graceful `integrated-local`. Note the framing (dropped push *obligation*, commits safe on local main) and the named non-goal (recovery-ref anchor + resume-push).

**Step 4: Add a `## What's New in v1.1.8` README.md section** above the v1.1.7 one.

**Step 5: Full workspace-manager suite once more.**
```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npx vitest run
```
Expected: all pass.

**Step 6: Stage.**
```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/pyproject.toml worker/.claude-plugin/plugin.json worker/.codex-plugin/plugin.json worker/mcp-servers/workspace-manager/package.json .claude-plugin/marketplace.json CHANGELOG.md README.md
```
