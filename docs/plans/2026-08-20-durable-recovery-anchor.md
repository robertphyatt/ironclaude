# Durable Recovery Anchor (C-1) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Stop `tombstoneTerminalAssignment` from deleting a private branch on the strength of a raw-SHA `recovery_ref`; upgrade a legacy raw-SHA row to a durable `refs/ironclaude/recovery/<guid>` before any branch deletion.

**Requirements:** docs/plans/2026-08-20-durable-recovery-anchor-requirements.md

**Architecture:** Add two private methods to `WorkspaceService` — `refIsDurableRef` (a `git show-ref --verify --quiet` actual-ref test, NOT `rev-parse`) and `ensureDurableRecoveryAnchor` (no-op for a ref-name `recovery_ref`; mint + DB-upgrade for a legacy raw SHA; throw for an unanchorable value) — and call the latter in the `abandoned` branch of `tombstoneTerminalAssignment` before branch deletion. Then rebuild the 3 dist bundles and run the full regression.

**Tech Stack:** TypeScript, better-sqlite3, vitest; git via `runGit`.

**Execution invariants (author + reviewer check against these):** shell state does not persist between steps (use literal absolute paths); Bash cwd is `commander/` (use `git -C <root>` / absolute paths); quote globs; foreground `sleep` is blocked; `docs/` is gitignored (`git add -f`); an empty result must be distinguishable from a failed command; commander pytest runs as explicit-file foreground batches with `-m "not destructive"` (a single background run is unreliable and was killed at 50% last loop); the `professional-mode-guard` treats a `|` inside a quoted grep pattern as a shell pipe — use single-term greps.

---

## Task 1: Durable-anchor gate + reader-side upgrade (RED→GREEN)

**Files:**
- Modify: `worker/mcp-servers/workspace-manager/src/workspace-service.ts` (add `refIsDurableRef` + `ensureDurableRecoveryAnchor` near `refResolves` at :644; call `ensureDurableRecoveryAnchor` in `tombstoneTerminalAssignment`'s abandoned path, before `removeWorktree`/`deleteTemporaryBranch`)
- Test: `worker/mcp-servers/workspace-manager/src/__tests__/workspace-service.test.ts`

**Step 1: Write the failing tests (RED).**

First, add `vi` to the vitest import at line 1 of `workspace-service.test.ts` (it currently
imports `{ afterEach, describe, expect, it }`):
```typescript
import { afterEach, describe, expect, it, vi } from 'vitest';
```
Then append these tests inside the existing top-level `describe` block in
`workspace-service.test.ts` (mirror the harness of the tests near line 1109–1154:
`repository()`, `initDb(join(root, '<name>.db'))`, `new WorkspaceService(database)`,
`manager.ensureSessionWorktree(...)`, `git(cwd, ...args)`, `writeFileSync`, `existsSync`,
`join`). A "legacy" row is constructed by rescue-abandoning (which mints the ref + removes
the worktree + keeps the branch), then simulating the DEPLOYED pre-fix state: delete the
minted recovery ref and overwrite `recovery_ref` with the bare SHA. Test (c) uses
`vi.spyOn(database, 'prepare')` to assert the no-op path issues no `recovery_ref` UPDATE.

```typescript
  it('tombstone upgrades a legacy raw-SHA recovery_ref (absent worktree) to a durable ref before deleting the branch', () => {
    const root = repository();
    const database = initDb(join(root, 'legacy-absent.db'));
    const manager = new WorkspaceService(database);
    const assignment = manager.ensureSessionWorktree({ repositoryPath: root, ownerSessionId: OWNER });
    const guid = assignment.workspace_guid;
    const branch = assignment.branch;
    writeFileSync(join(assignment.worktree_path, 'unintegrated.txt'), 'rescue me\n');
    manager.abandonWorkspace({ repositoryPath: root, workspaceGuid: guid, ownerSessionId: OWNER, mode: 'rescue' });
    const recoveryRef = `refs/ironclaude/recovery/${guid}`;
    const rescuedCommit = git(root, 'rev-parse', recoveryRef);
    // Simulate a DEPLOYED pre-fix legacy row: only the branch anchors the commit, and
    // recovery_ref is a bare SHA (no durable ref).
    git(root, 'update-ref', '-d', recoveryRef);
    database.prepare('UPDATE assignments SET recovery_ref = ? WHERE workspace_guid = ?').run(rescuedCommit, guid);
    expect(existsSync(assignment.worktree_path)).toBe(false);
    expect(git(root, 'branch', '--list', branch)).not.toBe('');

    expect(manager.cleanupWorkspace({ repositoryPath: root, workspaceGuid: guid, ownerSessionId: OWNER })
      .lifecycle_status).toBe('cleaned');

    // Branch deleted, but the rescued commit is now anchored by a durable ref that
    // survives the deletion, and the DB records the ref NAME, not the bare SHA.
    expect(git(root, 'branch', '--list', branch)).toBe('');
    expect(git(root, 'rev-parse', recoveryRef)).toBe(rescuedCommit);
    expect(git(root, 'cat-file', '-p', `${recoveryRef}:unintegrated.txt`)).toBe('rescue me');
    expect(database.prepare('SELECT recovery_ref FROM assignments WHERE workspace_guid = ?').get(guid))
      .toMatchObject({ recovery_ref: recoveryRef });
  });

  it('tombstone upgrades a legacy raw-SHA recovery_ref (present worktree) before deleting the branch', () => {
    const root = repository();
    const database = initDb(join(root, 'legacy-present.db'));
    const manager = new WorkspaceService(database);
    const assignment = manager.ensureSessionWorktree({ repositoryPath: root, ownerSessionId: OWNER });
    const guid = assignment.workspace_guid;
    const branch = assignment.branch;
    writeFileSync(join(assignment.worktree_path, 'recovery.txt'), 'recoverable\n');
    git(assignment.worktree_path, 'add', 'recovery.txt');
    git(assignment.worktree_path, 'commit', '-m', 'recoverable work');
    const rescuedCommit = git(assignment.worktree_path, 'rev-parse', 'HEAD');
    // Default abandon keeps the worktree on disk; set a bare-SHA recovery_ref (legacy).
    manager.abandonWorkspace({ repositoryPath: root, workspaceGuid: guid, ownerSessionId: OWNER });
    database.prepare('UPDATE assignments SET recovery_ref = ? WHERE workspace_guid = ?').run(rescuedCommit, guid);
    expect(existsSync(assignment.worktree_path)).toBe(true);

    expect(manager.cleanupWorkspace({ repositoryPath: root, workspaceGuid: guid, ownerSessionId: OWNER })
      .lifecycle_status).toBe('cleaned');

    const recoveryRef = `refs/ironclaude/recovery/${guid}`;
    expect(git(root, 'branch', '--list', branch)).toBe('');
    expect(git(root, 'rev-parse', recoveryRef)).toBe(rescuedCommit);
    expect(database.prepare('SELECT recovery_ref FROM assignments WHERE workspace_guid = ?').get(guid))
      .toMatchObject({ recovery_ref: recoveryRef });
  });

  it('tombstone leaves a normal ref-name recovery_ref untouched (no re-mint, no DB rewrite)', () => {
    const root = repository();
    const database = initDb(join(root, 'refname-noop.db'));
    const manager = new WorkspaceService(database);
    const assignment = manager.ensureSessionWorktree({ repositoryPath: root, ownerSessionId: OWNER });
    const guid = assignment.workspace_guid;
    const branch = assignment.branch;
    writeFileSync(join(assignment.worktree_path, 'unintegrated.txt'), 'rescue me\n');
    manager.abandonWorkspace({ repositoryPath: root, workspaceGuid: guid, ownerSessionId: OWNER, mode: 'rescue' });
    const recoveryRef = `refs/ironclaude/recovery/${guid}`;
    const rescuedCommit = git(root, 'rev-parse', recoveryRef);
    const before = database.prepare('SELECT recovery_ref FROM assignments WHERE workspace_guid = ?').get(guid) as { recovery_ref: string };
    expect(before.recovery_ref).toBe(recoveryRef);

    // R2 "no DB write" detector: no UPDATE touching recovery_ref may run while
    // cleaning up a ref-name row. updated_at cannot detect this — the lifecycle
    // transition unconditionally rewrites updated_at on the abandoned->cleaned tombstone.
    const prepareSpy = vi.spyOn(database, 'prepare');
    expect(manager.cleanupWorkspace({ repositoryPath: root, workspaceGuid: guid, ownerSessionId: OWNER })
      .lifecycle_status).toBe('cleaned');
    const recoveryRefWrites = prepareSpy.mock.calls
      .map((call) => String(call[0]))
      .filter((sql) => /update/i.test(sql) && /recovery_ref/i.test(sql));
    prepareSpy.mockRestore();
    expect(recoveryRefWrites).toEqual([]);

    const after = database.prepare('SELECT recovery_ref FROM assignments WHERE workspace_guid = ?').get(guid) as { recovery_ref: string };
    expect(after.recovery_ref).toBe(recoveryRef);
    expect(git(root, 'rev-parse', recoveryRef)).toBe(rescuedCommit);
    expect(git(root, 'branch', '--list', branch)).toBe('');
  });

  it('tombstone refuses (preserves) a raw-SHA recovery_ref whose object no longer exists', () => {
    const root = repository();
    const database = initDb(join(root, 'unanchorable.db'));
    const manager = new WorkspaceService(database);
    const assignment = manager.ensureSessionWorktree({ repositoryPath: root, ownerSessionId: OWNER });
    const guid = assignment.workspace_guid;
    const branch = assignment.branch;
    writeFileSync(join(assignment.worktree_path, 'unintegrated.txt'), 'rescue me\n');
    manager.abandonWorkspace({ repositoryPath: root, workspaceGuid: guid, ownerSessionId: OWNER, mode: 'rescue' });
    const recoveryRef = `refs/ironclaude/recovery/${guid}`;
    git(root, 'update-ref', '-d', recoveryRef);
    // A bare SHA that does not name any existing object.
    database.prepare('UPDATE assignments SET recovery_ref = ? WHERE workspace_guid = ?').run('1'.repeat(40), guid);

    expect(() => manager.cleanupWorkspace({ repositoryPath: root, workspaceGuid: guid, ownerSessionId: OWNER }))
      .toThrow(/preserving it/);
    // Row NOT tombstoned; the worker branch is preserved.
    expect(database.prepare('SELECT lifecycle_status FROM assignments WHERE workspace_guid = ?').get(guid))
      .toMatchObject({ lifecycle_status: 'abandoned' });
    expect(git(root, 'branch', '--list', branch)).not.toBe('');
  });
```

**Step 2: Run the new tests — verify RED.**

Run:
```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npx vitest run src/__tests__/workspace-service.test.ts -t "recovery_ref"
```

Expected: the two "legacy raw-SHA" upgrade tests FAIL — against current code the abandoned
proof passes on the bare SHA, the branch is deleted, and no `refs/ironclaude/recovery/<guid>`
is minted, so `git rev-parse`/`cat-file` on that ref errors. (The no-op and unanchorable
tests may already pass — they assert current-correct behavior.)

**Step 3: Implement the durable-anchor gate (GREEN).**

In `workspace-service.ts`, add these two private methods immediately after `refResolves`
(the method ending at :651):

```typescript
  /** True iff `ref` is an actual git ref (not merely a resolvable object such as a raw SHA). */
  private refIsDurableRef(root: string, ref: string): boolean {
    try {
      runGit(root, ['show-ref', '--verify', '--quiet', ref]);
      return true;
    } catch {
      return false;
    }
  }

  /**
   * Guarantees a durable git ref anchors an abandoned row's recovery commit before its
   * branch can be deleted. A ref-name `recovery_ref` is returned unchanged (no-op). A
   * legacy raw-SHA `recovery_ref` that still resolves to a reachable object is upgraded:
   * mint `refs/ironclaude/recovery/<guid>` at that commit and record the REF NAME. A
   * `recovery_ref` that is neither a durable ref nor a reachable object throws, so the
   * row and its branch are preserved (never-lose-work).
   */
  private ensureDurableRecoveryAnchor(repository: RepositoryLocation, assignment: Assignment): string {
    const current = assignment.recovery_ref;
    if (!current) throw new Error('Abandoned assignment lacks recovery evidence; preserving it');
    if (this.refIsDurableRef(repository.primaryCheckoutPath, current)) return current;
    if (!this.refResolves(repository.primaryCheckoutPath, current)) {
      throw new Error('Recovery evidence is neither a durable ref nor a reachable commit; preserving it');
    }
    const recoveryRef = `refs/ironclaude/recovery/${assignment.workspace_guid}`;
    runGit(repository.primaryCheckoutPath, ['update-ref', recoveryRef, current]);
    this.db.prepare(`
      UPDATE assignments SET recovery_ref = ?, updated_at = datetime('now') WHERE workspace_guid = ?
    `).run(recoveryRef, assignment.workspace_guid);
    return recoveryRef;
  }
```

Then, in `tombstoneTerminalAssignment`, insert the gate AFTER the abandoned/integrated
proof `if/else` block (the block that ends just before `if (present) removeWorktree(...)`)
and BEFORE `if (present) removeWorktree(...)`:

```typescript
    if (assignment.lifecycle_status === 'abandoned') {
      this.ensureDurableRecoveryAnchor(repository, assignment);
    }
```

**Step 4: Run the tests — verify GREEN.**

Run:
```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npx vitest run src/__tests__/workspace-service.test.ts
```

Expected: `Test Files 1 passed`; all tests in the file pass, including the four new ones.

**Step 5: Stage changes.**

Run:
```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/mcp-servers/workspace-manager/src/workspace-service.ts worker/mcp-servers/workspace-manager/src/__tests__/workspace-service.test.ts
```

Expected: both files staged (professional mode blocks commit).

---

## Task 2: Build + full regression + stage dist

**Files:**
- Modify: `worker/mcp-servers/workspace-manager/dist/index.js`
- Modify: `worker/mcp-servers/workspace-manager/dist/cli.js`
- Modify: `worker/mcp-servers/workspace-manager/dist/hook-intent.js`

**Depends on:** Task 1

No tests required: this task only rebuilds generated bundles and runs the existing suites.

**Step 1: Build.**

Run:
```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npm run build
```

Expected: tsc + 3 esbuild bundles; no type errors.

**Step 2: workspace-manager suite.**

Run:
```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npm test
```

Expected: `Test Files 8 passed (8)`; all green.

**Step 3: state-manager suite.**

Run:
```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager && npm test
```

Expected: `Test Files 13 passed (13)`; `Tests 196 passed (196)`.

**Step 4: Hook suites.**

Run:
```bash
for t in /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-*.sh; do echo "== $t =="; bash "$t" || echo "SUITE FAILED: $t"; done
```

Expected: each suite 0 failed; no `SUITE FAILED` line.

**Step 5: Commander pytest (explicit-file foreground batches, `-m "not destructive"`).**

A single `pytest -q` run is ~17 min I/O-bound and was killed mid-run last loop; run it as
four foreground batches, each under the Bash cap. The `ls` glob is absolute, so batch files
already hold absolute paths — do NOT prepend a prefix. Build the batch lists (portable
BSD/GNU `split -l`; `rm -f` defuses stale files from a prior run; the trailing `wc -l` is
in-band proof the batches exist and sum to the total):
```bash
rm -f /tmp/dra_tests.txt /tmp/dra_batch_* && ls /Users/roberthyatt/Code/ironclaude/commander/tests/test_*.py | sort > /tmp/dra_tests.txt && total=$(wc -l < /tmp/dra_tests.txt) && per=$(( (total + 3) / 4 )) && split -l "$per" /tmp/dra_tests.txt /tmp/dra_batch_ && wc -l /tmp/dra_tests.txt /tmp/dra_batch_*
```
Expected: 4 batch files `_aa`.._ad`, line counts summing to the `/tmp/dra_tests.txt` total.
(`ceil(total/4)` yields ≤4 batches; if the `wc -l` output shows only 3 files, run only those
— a missing `_ad` is not a failure.)

Then run each batch (repeat for `_aa`, `_ab`, `_ac`, `_ad` — the `test -s` guard makes a
silent full-suite run impossible; keep them as SEPARATE Bash calls, a single loop exceeds
the 10-min cap):
```bash
test -s /tmp/dra_batch_aa || { echo "FATAL: /tmp/dra_batch_aa missing/empty — refusing to run pytest without file args"; exit 1; } && cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q -m "not destructive" $(tr '\n' ' ' < /tmp/dra_batch_aa)
```

Expected: across the batches, 2867 passed, 2 deselected, 0 failed (the 2
`@pytest.mark.destructive` signal tests are inert-by-design and excluded by the default
suite; `-m "not destructive"` reproduces that).

**Step 6: Stage dist bundles.**

Run:
```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/mcp-servers/workspace-manager/dist/index.js worker/mcp-servers/workspace-manager/dist/cli.js worker/mcp-servers/workspace-manager/dist/hook-intent.js
```

Expected: the 3 bundles staged.

**Step 7: Return shell cwd to repo root.**

Run:
```bash
cd /Users/roberthyatt/Code/ironclaude
```

Expected: cwd is `/Users/roberthyatt/Code/ironclaude`.
