# Proactive Orphan-Offer Wiring + Sweep Pruning Implementation Plan (v1.1.12 corrective)

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Make the Brain's proactive per-orphan offer actually fire (daemon→Brain push carrying repo context) and make the reaper sweep prune vanished `orphan_surface` rows, resolving the two v1.1.12 Fable-review findings; then rebuild dist and re-verify.

**Requirements:** docs/plans/2026-09-22-proactive-orphan-discussion-requirements.md

**Architecture:** `_surface_preserved_orphans` gains one `self.brain.send_message(...)` on its existing change-gate (gated on `_orphaned_unmerged_count > 0`); `workflow.md`'s proactive step retriggers on that message; `reapAmbiguousOrphans` deletes `orphan_surface` rows for guids no longer live. No new tool/tick/table.

**Tech Stack:** Python (pytest), TypeScript (vitest, tsc), Markdown.

**Execution invariants:** shell state does not persist between steps; use absolute paths / `git -C <repo-root>`; `docs/` is gitignored (`-f`); no `2>/dev/null` on evidence; TDD RED/GREEN expecteds are structural.

---

## Task 1: daemon→Brain push in _surface_preserved_orphans + test

**Files:**
- Modify: `commander/src/ironclaude/main.py`
- Test: `commander/tests/test_worktree_reaper.py`

**Step 1 (RED): Add the Brain-notification test + update the existing surface fixtures.** In `test_worktree_reaper.py`, the three existing `_surface_preserved_orphans` tests build `SimpleNamespace(_orphaned_surface_state={}, slack=Mock())` (~:984, :1007, :1020) — add `brain=Mock(), _orphaned_unmerged_count=1` to each of those three `SimpleNamespace(...)` calls (the GREEN code reads `self._orphaned_unmerged_count` and `self.brain`, so without these the tests would `AttributeError`). Then add this new test in the same class:

```python
    def test_brain_notified_once_per_change_for_reviewable_set(self):
        """A surfaced set that has something needing review pushes the Brain one
        PRESERVED ORPHANS SURFACED notice per set-change; silent on a repeat, on an
        empty set, and on a squash-merged-only set (nothing to review)."""
        from types import SimpleNamespace

        from ironclaude.main import IroncladeDaemon

        fake = SimpleNamespace(
            _orphaned_surface_state={}, slack=Mock(), brain=Mock(),
            _orphaned_unmerged_count=1,
        )
        details = [{
            "id": "d1", "category": "genuinely-unmerged", "branch": "ironclaude/g1",
            "tip": "aaaaaaa1111", "evidence": "2 ahead of main", "repository_path": "/repo",
        }]
        IroncladeDaemon._surface_preserved_orphans(fake, details)
        assert fake.brain.send_message.call_count == 1
        msg = fake.brain.send_message.call_args.args[0]
        assert "PRESERVED ORPHANS SURFACED" in msg and "/repo" in msg and "d1" in msg

        IroncladeDaemon._surface_preserved_orphans(fake, list(details))
        assert fake.brain.send_message.call_count == 1  # repeat -> no re-notify

        fake_sm = SimpleNamespace(
            _orphaned_surface_state={}, slack=Mock(), brain=Mock(),
            _orphaned_unmerged_count=0,
        )
        sm = [{
            "id": "s1", "category": "squash-merged", "branch": "ironclaude/g2",
            "tip": "bbbbbbb2222", "evidence": "merged", "repository_path": "/repo",
        }]
        IroncladeDaemon._surface_preserved_orphans(fake_sm, sm)
        fake_sm.slack.post_message.assert_called_once()   # still listed in Slack
        fake_sm.brain.send_message.assert_not_called()    # but Brain not nagged

        fake_empty = SimpleNamespace(
            _orphaned_surface_state={}, slack=Mock(), brain=Mock(),
            _orphaned_unmerged_count=0,
        )
        IroncladeDaemon._surface_preserved_orphans(fake_empty, [])
        fake_empty.brain.send_message.assert_not_called()
```

Run:
```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_worktree_reaper.py -q -k "surface or brain_notified or change_gated or empty_details or category_change"
```
Expected: RED — `test_brain_notified_once_per_change_for_reviewable_set` fails (`brain.send_message.call_count` is 0, not 1); the three existing surface tests still pass.

**Step 2 (GREEN): Add the Brain send in `_surface_preserved_orphans`.** In `main.py`, in `_surface_preserved_orphans` (~:2011-2022), inside the existing `if current and changed:` block, after the `self.slack.post_message(...)` line, add:

```python
            if self._orphaned_unmerged_count > 0:
                by_repo: dict[str, list[str]] = {}
                for d in details:
                    by_repo.setdefault(d.get("repository_path", ""), []).append(
                        f"{d.get('id')} [{d.get('category')}]"
                    )
                repo_lines = "\n".join(
                    f"{repo}: {', '.join(ids)}" for repo, ids in by_repo.items()
                )
                self.brain.send_message(
                    f"PRESERVED ORPHANS SURFACED — {self._orphaned_unmerged_count} need review.\n"
                    f"{repo_lines}\n"
                    "Per 'Resolving Orphaned Worktrees': call "
                    "list_surfaced_orphans(repository_path) for each repo above and offer "
                    "the operator a per-orphan walkthrough once."
                )
```

Run the same pytest command. Expected: GREEN — the new test passes; the three existing surface tests still pass.

**Step 3: Stage.**
```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/main.py commander/tests/test_worktree_reaper.py
```
Expected: staged.

---

## Task 2: Retrigger the workflow.md proactive-offer step

**Files:**
- Modify: `commander/src/brain/rules/workflow.md`

**No tests required:** Brain guidance prose (no behavioral harness; validated by review + the daemon restart at deploy).

**Step 1: Replace the "Offer proactively" header + step 1 (~:279-280).** Replace exactly this block:

```markdown
**Offer proactively, on your next wake (once per set):**
1. On a normal wake, if there are surfaced orphans you have not already offered for this set, call `list_surfaced_orphans`. If it returns a non-empty set, offer once: "You have N preserved orphan(s) to review — want to walk through them?" Do NOT re-nag: if {OPERATOR_NAME} does not engage, wait for them to raise it rather than re-offering every wake.
```

with:

```markdown
**Offer proactively, when the daemon surfaces orphans (once per set):**
1. The daemon sends you a `PRESERVED ORPHANS SURFACED` message (naming each repo's `repository_path` and the orphan ids) exactly once per surfaced-set change. When you receive it, for each `repository_path` it lists, call `list_surfaced_orphans(repository_path)`; if it returns a non-empty set, offer once: "You have N preserved orphan(s) to review — want to walk through them?" This offer threads under the heartbeat as ordinary narration — do NOT add a directive reference or `[reply-to:]`, and do NOT phrase it as awaiting/waiting on a decision (that wording is captured as an operator-wait and never posted). Do NOT re-nag: if {OPERATOR_NAME} does not engage, wait for them to raise it — the daemon will not resend until the set changes.
```

(Leave step 2 — the operator-trigger — and the rest of the section unchanged.)

**Step 2: Stage.**
```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/src/brain/rules/workflow.md
```
Expected: staged.

---

## Task 3: Sweep prunes vanished orphan_surface rows + test

**Files:**
- Modify: `worker/mcp-servers/workspace-manager/src/workspace-service.ts`
- Test: `worker/mcp-servers/workspace-manager/src/__tests__/workspace-service.test.ts`

**Step 1 (RED): Add the pruning test.** In `workspace-service.test.ts`, inside the existing `describe('reapAmbiguousOrphans', ...)` block (helpers `repository`, `git`, `initDb`, `randomUUID`, `join`, `createOrphanWorktree`, `commitFile`, `orphanBranch` in scope), add:

```ts
    it('prunes the orphan_surface row when a surfaced orphan\'s branch and worktree are removed out-of-band', () => {
      const root = repository();
      const database = initDb(join(root, 'orphan-prune.db'));
      const manager = new WorkspaceService(database);
      const guid = randomUUID();
      const worktreePath = createOrphanWorktree(root, guid);
      commitFile(worktreePath, 'unmerged.txt', 'unmerged\n');

      const swept = manager.reapAmbiguousOrphans({ repositoryPath: root, ttlHours: 0 });
      expect(swept.preservedUnmerged).toEqual([orphanBranch(guid)]);
      const before = database.prepare(
        'SELECT 1 FROM orphan_surface WHERE repository_identity = ? AND workspace_guid = ?',
      ).get(swept.repositoryIdentity, guid);
      expect(before).toBeTruthy();

      // Remove the orphan OUTSIDE the tool chain.
      git(root, 'worktree', 'remove', '--force', worktreePath);
      git(root, 'branch', '-D', orphanBranch(guid));

      // The next sweep prunes the now-vanished row.
      manager.reapAmbiguousOrphans({ repositoryPath: root, ttlHours: 0 });
      const after = database.prepare(
        'SELECT 1 FROM orphan_surface WHERE repository_identity = ? AND workspace_guid = ?',
      ).get(swept.repositoryIdentity, guid);
      expect(after).toBeUndefined();
    });
```

Run:
```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npx vitest run src/__tests__/workspace-service.test.ts
```
Expected: RED — the new test fails at `expect(after).toBeUndefined()` because the un-pruned sweep leaves the stale row.

**Step 2 (GREEN): Prune vanished rows in `reapAmbiguousOrphans`.** In `workspace-service.ts`, in `reapAmbiguousOrphans`, immediately after the `guids` set is fully built (after the `for (const branch of listManagedBranches(...))` loop that adds branch guids), insert:

```ts
      // Prune surfaced rows whose orphan no longer exists at all (branch AND
      // worktree gone — e.g. removed out-of-band since it was surfaced); these
      // guids are never visited by the loop below, so nothing else would delete
      // them. This is the sweep, which is allowed to mutate.
      const surfacedRows = this.db.prepare(
        'SELECT workspace_guid FROM orphan_surface WHERE repository_identity = ?',
      ).all(repository.repositoryIdentity) as Array<{ workspace_guid: string }>;
      for (const { workspace_guid } of surfacedRows) {
        if (!guids.has(workspace_guid)) {
          this.deleteOrphanSurface(repository.repositoryIdentity, workspace_guid);
        }
      }
```

Run the same vitest command. Expected: GREEN — the new test passes; the existing `listSurfacedOrphans` liveness tests and every other `reapAmbiguousOrphans` test still pass (a live orphan's guid is in `guids`, so its row is not pruned).

**Step 3: Stage.**
```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/mcp-servers/workspace-manager/src/workspace-service.ts worker/mcp-servers/workspace-manager/src/__tests__/workspace-service.test.ts
```
Expected: staged.

---

## Task 4: Rebuild dist + full-suite verification

**Files:**
- Modify (build outputs): `worker/mcp-servers/workspace-manager/dist/cli.js`, `worker/mcp-servers/workspace-manager/dist/index.js`, `worker/mcp-servers/workspace-manager/dist/hook-intent.js`

**Depends on:** Tasks 1, 2, 3.

**No tests required:** build + full-suite verification task.

**Step 1: Rebuild.**
```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npm run build
```
Expected: `tsc` + bundle succeed, no error.

**Step 2: Full workspace-manager vitest.**
```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npx vitest run
```
Expected: `passed | 0 failed` (the `onTaskUpdate` RPC-timeout line is benign; judge by `0 failed`).

**Step 3: Full Commander pytest.**
```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q
```
Expected: `0 failed`.

**Step 4: Stage rebuilt dist.**
```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/mcp-servers/workspace-manager/dist/cli.js worker/mcp-servers/workspace-manager/dist/index.js worker/mcp-servers/workspace-manager/dist/hook-intent.js
```
Expected: staged.

---

## After execution (operator-gated, not part of the plan)

Re-squash this corrective into the unpushed v1.1.12 (`c0b42c2`), refresh the CHANGELOG/code-comment wording (the pruning + proactive claims are now true), re-review, then deploy (dist → both caches; Commander restart for `main.py` + `workflow.md`).
