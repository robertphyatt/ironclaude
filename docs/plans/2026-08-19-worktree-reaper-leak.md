# Worktree Reaper — Leak Closure Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Let the Commander reaper reclaim leaked ownerless managed worktrees (and tombstone gone-worktree rows), preserving unintegrated work on a durable git ref first — closing the worktree leak with no operator git commands.

**Requirements:** docs/plans/2026-08-19-worktree-reaper-leak-requirements.md

**Architecture:** Reuse `rescueAbandon` (preserve) + `cleanupWorkspace` (tombstone). Make preserve DURABLE: `rescueAbandon` mints `refs/ironclaude/recovery/<guid>` so the temporary branch can later be deleted without stranding the rescued commit. Extract a private gone-tolerant `tombstoneTerminalAssignment` that deletes the branch only once a durable recovery ref exists; a reaper-only `reapLeakedAssignment` resolves by GUID (no owner match, but keeps the observed-identity guard for a present worktree), preserves durably, then tombstones. The Commander reaper's ownerless branch calls a new `reap` verb (local transport `{}`).

**Tech Stack:** TypeScript (workspace-manager, vitest), Python (Commander, pytest).

**Execution invariants (for the blind reviewer):** Shell state does not persist between steps; absolute paths / `git -C`. TDD RED→GREEN. Never-lose-work is proven by asserting the recovery ref resolves to the rescued commit AFTER the branch is deleted (`git rev-parse` alone before gc does not prove durability — assert the ref exists). No pre-measured counts asserted.

---

## Task 1: workspace-service.ts — durable preserve + gone-tolerant tombstone + reaper reap

**Files:**
- Modify: `worker/mcp-servers/workspace-manager/src/workspace-service.ts`
- Test: `worker/mcp-servers/workspace-manager/src/__tests__/workspace-service.test.ts`

**Step 1 (RED): tests** in `workspace-service.test.ts` (real temp repo):
- `rescueAbandon anchors the rescued commit on a durable ref that survives branch deletion`: rescue an active assignment with an uncommitted change, then `git branch -D ironclaude/<guid>` in the primary checkout, then assert `git rev-parse refs/ironclaude/recovery/<guid>` resolves to the rescued commit and the change is in it.
- `cleanupWorkspace tombstones a rescue-abandoned row (branch deleted, ref preserved)`: after `abandonWorkspace(mode:'rescue')`, call `cleanupWorkspace({...owner})`, expect `cleaned` and `refs/ironclaude/recovery/<guid>` still resolves.
- `reapLeakedAssignment preserves then tombstones an ownerless active row (present worktree)`; `reapLeakedAssignment tombstones an ownerless row whose worktree is gone but branch survives (mints ref at branch tip)`; `reapLeakedAssignment on an ownerless row with worktree AND branch gone records base_commit and tombstones`; `reapLeakedAssignment refuses+preserves a present worktree checked out on a FOREIGN branch`; `reapLeakedAssignment routes a reserved never-materialized row to row-delete`.

Run one RED:
```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npx vitest run src/__tests__/workspace-service.test.ts -t "durable ref that survives branch deletion" --testTimeout=30000
```
Expected: FAIL (recovery_ref is a raw SHA; no ref minted).

**Step 2 (GREEN — R0): `rescueAbandon` mints a durable ref.** In `rescueAbandon` (:607-610) replace the raw-SHA store:
```typescript
      const rescuedHead = worktreeHead(assignment.worktree_path);
      const recoveryRef = `refs/ironclaude/recovery/${assignment.workspace_guid}`;
      runGit(repository.primaryCheckoutPath, ['update-ref', recoveryRef, rescuedHead]);
      this.db.prepare(`
        UPDATE assignments SET recovery_ref = ?, updated_at = datetime('now') WHERE workspace_guid = ?
      `).run(recoveryRef, assignment.workspace_guid);
```
(Store the REF NAME. The commit now has two anchors — the branch and this ref — so a later branch delete cannot strand it. Update the `rescueAbandon` docstring accordingly.)

**Step 3 (GREEN — R1): private `tombstoneTerminalAssignment(repository, assignment)`.** Carry `cleanupWorkspace`'s CURRENT proof predicates verbatim for the present case, gate the on-disk ops on presence, and delete the branch only after a durable ref/integration:
```typescript
  private tombstoneTerminalAssignment(repository: RepositoryLocation, assignment: Assignment): Assignment {
    const present = existsSync(assignment.worktree_path)
      && worktreeExists(repository.primaryCheckoutPath, assignment.worktree_path);
    if (assignment.lifecycle_status === 'abandoned') {
      if (!assignment.recovery_ref) throw new Error('Abandoned worktree lacks recovery evidence; preserving it');
      if (present) {
        if (!worktreeIsClean(assignment.worktree_path)) throw new Error('Managed worktree is dirty; preserving it');
        if (!isAncestor(repository.primaryCheckoutPath, worktreeHead(assignment.worktree_path), assignment.recovery_ref)) {
          throw new Error('Managed worktree HEAD not reachable from recovery evidence; preserving it');
        }
      } else if (!refResolves(repository.primaryCheckoutPath, assignment.recovery_ref)) {
        throw new Error('Recovery evidence does not resolve; preserving it');
      }
    } else { // integrated — copy the current integrated proof block from cleanupWorkspace VERBATIM, guarding the actualHead check on `present`
      // ... integration_records lookup + target/commit/ancestor checks exactly as :660-674 ...
    }
    if (present) removeWorktree(repository.primaryCheckoutPath, assignment.worktree_path);
    deleteTemporaryBranch(repository.primaryCheckoutPath, assignment.branch);
    return transitionAssignment(this.db, assignment.workspace_guid, assignment.lifecycle_status, 'cleaned');
  }
```
Add a small helper `refResolves(root, ref): boolean` (try `runGit(root, ['rev-parse','--verify','--quiet', ref])`, false on throw) OR reuse an existing rev-parse helper if one exists in git.ts — confirm and use the existing one if present. The present-abandoned proof MUST equal the current `:656` predicate (`isAncestor(actualHead, recovery_ref)`) so the existing green test stays green; integrated block copied verbatim from `:660-674` with only the `actualHead` line guarded on `present`.

**Step 4 (GREEN — R1): rewire `cleanupWorkspace`** to keep `getWorkspaceAssignment` (owner match) + the `integrated|abandoned` guard + present-case `validateManagedIdentity`, then `return this.tombstoneTerminalAssignment(repository, assignment)`. Present-worktree behavior is byte-identical.

**Step 5 (GREEN — R2): `reapLeakedAssignment`.**
```typescript
  reapLeakedAssignment(input: { repositoryPath: string; workspaceGuid: string }): Assignment {
    const repository = discoverRepository(input.repositoryPath);
    const assignment = getAssignment(this.db, input.workspaceGuid);
    if (!assignment || assignment.repository_identity !== repository.repositoryIdentity) {
      throw new Error('Leaked assignment binding does not match repository');
    }
    if (assignment.worktree_path !== managedWorktreePath(repository.primaryCheckoutPath, assignment.workspace_guid)
      || assignment.branch !== managedBranch(assignment.workspace_guid)) {
      throw new Error('Leaked assignment does not match canonical managed identity');
    }
    if (assignment.lifecycle_status === 'cleaned') return assignment;
    if (assignment.lifecycle_status === 'reserved') return this.cleanupReservedAssignment_forReaper(repository, assignment); // delete never-materialized row (reuse cleanupReservedAssignment's proof: no worktree on disk)
    const present = existsSync(assignment.worktree_path)
      && worktreeExists(repository.primaryCheckoutPath, assignment.worktree_path);
    if (present) this.validateManagedIdentity(repository, assignment); // observed-branch guard; throws on foreign branch → surfaced upstream
    if (assignment.lifecycle_status !== 'integrated' && assignment.lifecycle_status !== 'abandoned') {
      if (present) {
        this.rescueAbandon(repository, assignment);                         // mints recovery ref, removes dir, →abandoned
      } else {
        const recoveryRef = `refs/ironclaude/recovery/${assignment.workspace_guid}`;
        if (branchResolves(repository.primaryCheckoutPath, assignment.branch)) {
          runGit(repository.primaryCheckoutPath, ['update-ref', recoveryRef, `refs/heads/${assignment.branch}`]);
        } else {
          runGit(repository.primaryCheckoutPath, ['update-ref', recoveryRef, assignment.base_commit]); // nothing preservable; base_commit is NOT NULL + always reachable
        }
        this.db.prepare(`UPDATE assignments SET recovery_ref = ?, updated_at = datetime('now') WHERE workspace_guid = ?`).run(recoveryRef, assignment.workspace_guid);
        transitionAssignment(this.db, assignment.workspace_guid, assignment.lifecycle_status, 'abandoned');
      }
    }
    return this.tombstoneTerminalAssignment(repository, getAssignment(this.db, input.workspaceGuid)!);
  }
```
(`branchResolves`/`refResolves` = the same rev-parse helper. `cleanupReservedAssignment` at :624 takes an owner-matched `AssignmentRequest`; for the reaper add a tiny private variant that runs its exact proof — `lifecycle_status==='reserved'` + no worktree on disk — and deletes the row, WITHOUT the owner match. NEVER transition `→abandoned` before the recovery ref is written.)

**Step 6:** run the full test file GREEN + `npx tsc --noEmit` (exit 0). **Step 7:** stage `workspace-service.ts` + `workspace-service.test.ts`.

---

## Task 2: cli.ts — `reap` verb

**Files:** Modify `cli.ts` + Test `cli.test.ts` + Test `tool-dispatch.test.ts` (its `internalDependencies()` literal must gain a `reap` mock because `reap` becomes a REQUIRED interface member, else tsc TS2741). **Depends on:** Task 1.

- RED: `cli.test.ts` — dispatching `reap` with `{repository_path, workspace_guid}` (no owner) calls `service.reapLeakedAssignment` with those (3 tests: NAMES contains reap, dispatch routes to `dependencies.reap` once, handler calls `reapLeakedAssignment`).
- GREEN `cli.ts`: extend `InternalCommandDependencies` (:19-27) with `reap`; add `'reap'` to `INTERNAL_COMMAND_NAMES` (:29); add `case 'reap': return dependencies.reap(args);` after `case 'cleanup'` (:101); add the `reap:` entry near the `cleanup` dependency (:166) using `requiredString(args, 'repository_path')` / `requiredString(args, 'workspace_guid')` (note: `requiredString`, cli.ts:52 — NOT `requireString`). No `ownerSessionId`.
- GREEN `tool-dispatch.test.ts`: add `reap: vi.fn()` (sibling mock shape) to its `internalDependencies()` literal so the `InternalCommandDependencies` object is complete for tsc.
- GREEN: `npx vitest run src/__tests__/cli.test.ts src/__tests__/tool-dispatch.test.ts --testTimeout=30000 && npx tsc --noEmit` → pass, exit 0. Stage all three.

---

## Task 3: WorkspaceClient.reap + reaper ownerless wiring

**Files:**
- Modify: `commander/src/ironclaude/workspace_client.py`
- Modify: `commander/src/ironclaude/main.py`
- Test: `commander/tests/test_workspace_client.py`
- Test: `commander/tests/test_worktree_reaper.py`

**Depends on:** Task 2.

- RED: (a) `test_workspace_client.py` — `client.reap(payload, **transport)` invokes the `reap` CLI command (mirror the existing `cleanup` test); (b) `test_worktree_reaper.py` — a NEW reap-success test: an ownerless active leaked TTL-eligible candidate whose `worktree_path` is a real managed path → reaper calls `workspace_client.reap` with the derived `repository_path` and `counts["released"]` increments. This REQUIRES extending `_insert_assignment` (:137, currently hardcodes `worktree_path='/wt'`) with a `worktree_path` parameter; the new test passes `/repo/.ironclaude/worktrees/<guid>` so derivation succeeds. Do NOT change the shared `/wt` default — the two existing ownerless `/wt` tests (:401, :417) must keep surfacing (derivation raises on the marker-less `/wt` path).
- GREEN (C2): in `workspace_client.py` add `"reap"` to `_COMMANDS` (:16) and a method `def reap(self, payload, **transport): return self._invoke("reap", payload, **transport)` (mirror `cleanup` :231).
- GREEN (C3+R3): add `_reap_ownerless_assignment(workspace_client, assignment, transport)` deriving `repository_path` from `worktree_path` by splitting on `"/.ironclaude/worktrees/"` (raise if underivable) and calling `workspace_client.reap({"repository_path":…, "workspace_guid":…}, **transport)`. In the reaper ownerless branch (main.py:466-479) replace the surfaced-only body with `try: _reap_ownerless_assignment(workspace_client, assignment, {}); counts["released"] += 1` (transport `{}`, NOT `resolve_transport(None)`) `except Exception:` keep the existing surfaced warning + `counts["surfaced"] += 1`. (Optional Boy-Scout: harden `_worktree_reap_transport` :1636 handler to `(worker or {}).get("id")`.)
- GREEN (test clarity): in `test_worktree_reaper.py` (a) rewrite the module docstring paragraph (~lines 9-24) — ownerless rows are now RELEASED via the owner-free `reap` verb; surfacing is the FALLBACK when `repository_path` is underivable from `worktree_path` (or the reap call fails); (b) rename `test_reserved_ownerless_row_is_surfaced_not_force_called` → `test_ownerless_reserved_underivable_path_stays_surfaced` and `test_ownerless_active_row_is_surfaced_not_force_called` → `test_ownerless_active_underivable_path_stays_surfaced`, keep all existing asserts and ADD `client.reap.assert_not_called()` to each (the `/wt` fixture has no marker so derivation raises before any CLI call).
- GREEN: `cd .../commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_workspace_client.py tests/test_worktree_reaper.py -q` → pass. Stage all four files.

---

## Task 4: Build + full regression + stage dist

**Files:** Modify `dist/index.js`, `dist/cli.js`, `dist/hook-intent.js`. **Depends on:** Task 3.

1. `cd .../workspace-manager && npm run build` → tsc + 3 bundles, no errors.
2. `npm test` (workspace-manager) → 8 files green.
3. `cd .../state-manager && npm test` → 13 files, 196 passed.
4. hook suites loop → each 0 failed.
5. `cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q` → all pass (run to completion, no background watcher).
6. Stage the 3 dist bundles.
7. `cd /Users/roberthyatt/Code/ironclaude` (return shell to repo root).
