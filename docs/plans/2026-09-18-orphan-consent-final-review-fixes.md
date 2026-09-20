# Orphan-Consent-Cleanup Final-Review Fixes Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Fix the 2 Important findings (I1 unbounded patch-id scan, I2 `--force` bound to sweep-refreshed category) plus 2 cheap observations (obs#1 CAS mislabel, obs#4 count mismatch) from the final tier-up Fable end review of the combined staged diff.

**Requirements:** docs/plans/2026-09-18-orphan-consent-final-review-fixes-requirements.md

**Design:** docs/plans/2026-09-18-orphan-consent-final-review-fixes-design.md

**Architecture:** Bound `contentMergedInto`'s squash detection to a fixed spawn count (single pathspec-restricted `git log -p` piped into one `git patch-id --stable`; reorder tells `cherry → reverseApply → patchIdAggregate`; `--no-ext-diff` on both diff sources; gate the origin double-scan on `!isAncestor(originRef, target)`), preserving detection as a strict superset. Revalidate the operator-consented `category` before a `--force` reap. Return a distinct `target-moved` CAS outcome, and teach the Brain to carry the surfaced `category` tag so consent-gated dirty reaps stay reachable. Make the Slack surface header count the same need-review set as the heartbeat.

**Tech Stack:** TypeScript (workspace-manager MCP server, vitest), Python (commander daemon, pytest), git plumbing.

**Execution invariants (the reviewer checks commands against these):**
- Shell state does NOT persist between steps — every command uses literal absolute paths.
- This session's cwd base is the repo root `/Users/roberthyatt/Code/ironclaude`; every command `cd`s to an absolute dir and stages via `git -C /Users/roberthyatt/Code/ironclaude`.
- `docs/` is gitignored — plan/findings artifacts staged with `git add -f`.
- No `2>/dev/null` on evidence; list names not counts where absence must be provable.
- Every guard names the broken state it catches; TDD RED→GREEN→stage for executable code.
- Ground all git-command behavior (patch-id stream parsing, `--literal-pathspecs`, `--no-ext-diff`, empty-tree root diff, `update-ref` CAS exit code) against live git during execution.

---

## Task 1: I1 — bound `patchIdAggregateTellMerged` (git.ts)

**Files:**
- Modify: `worker/mcp-servers/workspace-manager/src/git.ts` (`patchId` :553-566, `patchIdAggregateTellMerged` :599-613, `contentMergedInto` :669-674)
- Test: `worker/mcp-servers/workspace-manager/src/__tests__/git-content-merged.test.ts`

**Step 1 (RED):** Add tests. A pass-through `spawnSync` counter (`vi.mock('node:child_process', …)` that records every call and delegates to the real `spawnSync`; git.ts imports `spawnSync` at :1 so the counter observes its git calls; the test's own `execFileSync` helpers stay real).
- **Bound proof:** a genuinely-unmerged **2-file** branch; advance `main` by K commits (half touching the branch's files). Run `contentMergedInto(root, tip, 'main', scratch)` at **K=5** and **K=60**. Assert the per-call recorded `spawnSync` count is **equal** at K=5 and K=60 and **≤ 12**, AND the K=5 recorded calls include one entry containing `'patch-id'` and one containing `'cherry'` (proves the counter actually observed git.ts's tells — a no-op counter's `0===0 && 0<=12` would otherwise pass GREEN).
- **Detection preserved:** a **≥2-commit** branch whose commits have distinct diffs (e.g. create a file with line 1, then append line 2 — the existing pattern at :52-59), squash-merged into `main`, then a LATER `main` commit edits the same lines. First assert the precondition `git cherry main <tip>` lists every commit with a leading `'+ '` (cherry-dark — so a `true` result is attributable to the patch-id tell, not to `cherry`); then expect `contentMergedInto` `true`.
- **Partial landing bounds the widening:** a two-file branch; a `main` commit lands only one file identically → `false` (no commit-count constraint needed).
- **Literal pathspec:** a **≥2-commit** distinct-diff branch touching a file named `weird[1].txt`, squash-merged + a later overlapping edit, cherry-dark precondition → `true`. (Falsifiable post-fix: dropping `--literal-pathspecs` makes `weird[1].txt` a bracket-glob matching `weird1.txt`, so the walk finds nothing → `false`.)
- **Empty-diff tip** (commit then revert) → `false`.
- Keep the existing squash / genuinely-unmerged / no-object-writes cases (:44-94) green.

Run:
```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npx vitest run src/__tests__/git-content-merged.test.ts
```
Expected: RED — **only the bound-proof case fails** against the current per-commit implementation (K=60's spawn count exceeds K=5's by ≈2×55). Detection-preserved, partial-landing, literal-pathspec, and empty-diff pass pre-fix (the per-commit walk at :603 uses no pathspec) and serve as regression guards; literal-pathspec becomes falsifiable only post-fix.

**Step 2 (GREEN):** Implement the bound.
- In `patchId` (:554) add `--no-ext-diff`: `['-C', cwd, 'diff', '--no-ext-diff', revA, revB]`.
- Rewrite `patchIdAggregateTellMerged`:
  - keep `aggregateId = patchId(mergeBase, tip)`; `null` → return false;
  - `P` = NUL-split of `git diff --name-only --no-renames -z <mergeBase> <tip>`; if `P` is empty → return false;
  - one walk: `git --literal-pathspecs log --no-merges --no-ext-diff --format=commit %H -p <mergeBase>..<targetRef> -- <P…>` with `maxBuffer` (e.g. `64 * 1024 * 1024`); on `error` throw (→ `tryTell` → false);
  - pipe its stdout as `input` into one `git patch-id --stable`; parse each `<patch-id> <sha>` line;
  - return true iff any emitted patch-id `=== aggregateId`.
- In `contentMergedInto` reorder tells to `cherry → reverseApply → patchIdAggregate`.

**Step 3 (verify):**
```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npx vitest run src/__tests__/git-content-merged.test.ts && npx tsc --noEmit
```
Expected: 0 failed; tsc clean.

**Step 4 (stage):**
```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/mcp-servers/workspace-manager/src/git.ts worker/mcp-servers/workspace-manager/src/__tests__/git-content-merged.test.ts
```

---

## Task 2: obs#4 — surface header counts the need-review set (notifications.py)

**Files:**
- Modify: `commander/src/ironclaude/notifications.py` (`format_orphaned_orphans` :212-229)
- Test: `commander/tests/test_notifications.py`

No I1/I2 dependency — independent (Wave 1).

**Step 1 (RED):** Add tests:
- `details` with one `squash-merged` entry + two non-squash-merged entries → the header's need-review number is **2** (not 3); all three entries still appear as bullets.
- All-`squash-merged` details (e.g. two entries) → the header's need-review number is **0** while both bullets still appear.

Run:
```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_notifications.py -q
```
Expected: RED — header currently uses `len(details)` (counts squash-merged).

**Step 2 (GREEN):** In `format_orphaned_orphans`, compute the header's need-review count as the number of `details` whose `category != "squash-merged"` (matching the heartbeat's `_orphaned_unmerged_count` at main.py:1940-1942). Keep listing every entry; when squash-merged entries exist, the header may note them (e.g. "(+N already squash-merged, listed below)") but the need-review number excludes them.

**Step 3 (verify):**
```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_notifications.py -q
```
Expected: 0 failed.

**Step 4 (stage):**
```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/notifications.py commander/tests/test_notifications.py
```

---

## Task 3: I1 — gate the origin double-scan + shared spawn wrapper (workspace-service.ts)

**Files:**
- Modify: `worker/mcp-servers/workspace-manager/src/workspace-service.ts` (`classifyPreservedOrphan` :1322-1340)
- Test: `worker/mcp-servers/workspace-manager/src/__tests__/workspace-service.test.ts`

**Depends on:** Task 1 (the bounded, reordered tells).

**Step 1 (RED):** First add the shared `spawnSync` wrapper this file's later tasks reuse (define once here):
```ts
const spawnControl = vi.hoisted(() => ({
  calls: [] as string[][],
  beforeSpawn: null as null | ((args: readonly string[]) => void),
}));
vi.mock('node:child_process', async (importOriginal) => {
  const actual = await importOriginal<typeof import('node:child_process')>();
  return {
    ...actual,
    spawnSync: (cmd: string, args?: readonly string[], opts?: unknown) => {
      spawnControl.calls.push([cmd, ...(args ?? [])]);
      spawnControl.beforeSpawn?.(args ?? []);
      return (actual.spawnSync as unknown as Function)(cmd, args, opts);
    },
  };
});
```
Reset `spawnControl.calls.length = 0; spawnControl.beforeSpawn = null;` in `afterEach`. It is pass-through, so the file's existing ~2400 lines are unaffected and the `execFileSync` helper stays real.

Then add:
- **`merged-on-origin` positive (currently untested):** squash the orphan onto a temp branch, `git update-ref refs/remotes/origin/main <that sha>` with local `main` behind → category `merged-on-origin`.
- **Gate skip when origin at/behind:** `refs/remotes/origin/main` == local `main`, a genuinely-unmerged orphan → category `genuinely-unmerged` AND assert `spawnControl.calls` contains **exactly one** entry equal to `['-C', <primary>, 'merge-base', '--is-ancestor', 'refs/remotes/origin/main', 'refs/heads/main']` and **no** recorded entry containing both `'cherry'` and `'refs/remotes/origin/main'` (proves the second `contentMergedInto` did not run against origin). (This args-inspection replaces a brittle absolute spawn count.)

Run:
```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npx vitest run src/__tests__/workspace-service.test.ts
```
Expected: RED — the gate-skip case fails: today the origin pass always runs when `originRef` resolves, so a `cherry` against `refs/remotes/origin/main` is recorded.

**Step 2 (GREEN):** In `classifyPreservedOrphan`, run the origin pass only when `originRef` resolves AND `!isAncestor(repository.primaryCheckoutPath, originRef, target)`.

**Step 3 (verify):**
```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npx vitest run src/__tests__/workspace-service.test.ts && npx tsc --noEmit
```
Expected: 0 failed; tsc clean.

**Step 4 (stage):**
```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/mcp-servers/workspace-manager/src/workspace-service.ts worker/mcp-servers/workspace-manager/src/__tests__/workspace-service.test.ts
```

---

## Task 4: I2 — category-consented `--force` reap (workspace-service.ts + cli.ts)

**Files:**
- Modify: `worker/mcp-servers/workspace-manager/src/workspace-service.ts` (`OrphanResolutionRequest` :167-182, `resolveOneOrphan` reap path :1176-1188)
- Modify: `worker/mcp-servers/workspace-manager/src/cli.ts` (`requiredResolutions` :122-160)
- Test: `worker/mcp-servers/workspace-manager/src/__tests__/workspace-service.test.ts`, `.../cli.test.ts`, `.../tool-dispatch.test.ts`

**Depends on:** Task 3 (serialize workspace-service.ts edits; reuse the spawn wrapper if needed).

**Step 1 (RED):** The defect only manifests once the sweep has flipped `row.category` to `dirty` while the operator's consent named a non-dirty category. Use a **two-sweep** setup for the stale-consent cases:
- Setup: create the orphan worktree + commit unmerged work; sweep #1 `reapAmbiguousOrphans({ ttlHours: 0 })` → assert `preservedDetail[guid].category === 'genuinely-unmerged'`; write an uncommitted file into the worktree (tip unchanged); sweep #2 → assert `preservedDirty` contains the branch AND `SELECT category, tip FROM orphan_surface` (existing query pattern) is `('dirty', <same tip>)`.
- **Case 1 (stale consent, RED):** `resolveOrphan({ resolutions: [{ guid, action: 'reap', category: 'genuinely-unmerged' }] })` → `refused-changed`; worktree, uncommitted file, and branch still present. Pre-fix: `row.category === 'dirty'` → force → `reaped`.
- **Case 2 (regression guard):** after the two-sweep setup, `reap` with `category: 'dirty'` → `reaped` (force-removed), worktree gone. Passes pre and post.
- **Case 3 (no category, RED):** after the two-sweep setup, `reap` with NO `category` → `refused-changed`. Pre-fix: `reaped`.
- **Case 4 (AND-conjunct guard):** surfaced `genuinely-unmerged`, worktree dirtied, **no second sweep** (row stays `genuinely-unmerged`); `reap` with `category: 'dirty'` → `refused-changed` (a correct impl requires `row.category === 'dirty'` too). Guards against an impl that gates on `resolution.category` alone.
- cli.test.ts: a resolution with `category: 'dirty'` reaches the service call with `category: 'dirty'`; an empty/non-string category is rejected.

Run:
```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npx vitest run src/__tests__/workspace-service.test.ts src/__tests__/cli.test.ts src/__tests__/tool-dispatch.test.ts
```
Expected: RED — cases 1 and 3 return `reaped` pre-fix (force fires on `row.category` alone at :1181-1182); after the fix they return `refused-changed`.

**Step 2 (GREEN):**
- Add `category?: 'squash-merged' | 'merged-on-origin' | 'genuinely-unmerged' | 'dirty'` to `OrphanResolutionRequest`.
- `cli.ts requiredResolutions`: read `record.category`; when present, require a non-empty string in the known set; output `category` (mirroring the `integration_target → integrationTarget` handling at :150-157).
- `resolveOneOrphan` reap path: set `force = true` only when `present && !worktreeIsClean(worktreePath) && row.category === 'dirty' && resolution.category === 'dirty'`; when the worktree is dirty but that condition is not met, return `refused-changed`.

**Step 3 (verify):**
```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npx vitest run src/__tests__/workspace-service.test.ts src/__tests__/cli.test.ts src/__tests__/tool-dispatch.test.ts && npx tsc --noEmit
```
Expected: 0 failed; tsc clean.

**Step 4 (stage):**
```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/mcp-servers/workspace-manager/src/workspace-service.ts worker/mcp-servers/workspace-manager/src/cli.ts worker/mcp-servers/workspace-manager/src/__tests__/workspace-service.test.ts worker/mcp-servers/workspace-manager/src/__tests__/cli.test.ts worker/mcp-servers/workspace-manager/src/__tests__/tool-dispatch.test.ts
```

---

## Task 5: obs#1 — distinct `target-moved` CAS outcome (workspace-service.ts)

**Files:**
- Modify: `worker/mcp-servers/workspace-manager/src/workspace-service.ts` (`mergeOrphanThenReap` CAS-failure return — identify the site by its `// target moved concurrently` comment, since earlier tasks shift line numbers)
- Test: `worker/mcp-servers/workspace-manager/src/__tests__/workspace-service.test.ts`

**Depends on:** Task 4 (serialize workspace-service.ts edits).

**Step 1 (RED):** The CAS `expected` value is snapshotted (`rev-parse target^{commit}`) inside `mergeOrphanThenReap` at resolution time, and the `update-ref target newCommit expected` runs synchronously ~37 lines later — so the target must be advanced BETWEEN those two points to make the CAS fail. Use the Task-3 spawn wrapper's `beforeSpawn` hook:
- Surface a clean genuinely-unmerged orphan; `const expected = git(root, 'rev-parse', 'refs/heads/main')`.
- `let fired = false; let concurrent = ''; spawnControl.beforeSpawn = (args) => { if (!fired && args.includes('update-ref') && args.includes('refs/heads/main') && args[args.length - 1] === expected) { fired = true; concurrent = git(root, 'commit-tree', expected + '^{tree}', '-p', expected, '-m', 'concurrent'); git(root, 'update-ref', 'refs/heads/main', concurrent, expected); } };`
- `resolveOrphan({ resolutions: [{ guid, action: 'merge-then-reap' }] })`.
- Assert: `fired === true` (non-vacuous); outcome `target-moved` (pre-fix `refused-changed` → RED); `git rev-parse refs/heads/main === concurrent` (not clobbered); the orphan branch tip unchanged; worktree present.
- Then `spawnControl.beforeSpawn = null` and re-run the same resolution → `merged-then-reaped` with `main` advanced past `concurrent` (proves the "plain retry succeeds" semantics that distinguish `target-moved` from `refused-changed`).

Run:
```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npx vitest run src/__tests__/workspace-service.test.ts
```
Expected: RED — the CAS-failure return is `refused-changed` today.

**Step 2 (GREEN):** At the CAS-failure return (the `// target moved concurrently` line) return `{ id, guid, outcome: 'target-moved' }`.

**Step 3 (verify):**
```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npx vitest run src/__tests__/workspace-service.test.ts && npx tsc --noEmit
```
Expected: 0 failed; tsc clean.

**Step 4 (stage):**
```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/mcp-servers/workspace-manager/src/workspace-service.ts worker/mcp-servers/workspace-manager/src/__tests__/workspace-service.test.ts
```

---

## Task 6: Outcome-contract + category docs, incl. Brain category source (orchestrator_mcp.py, workflow.md, README.md)

**Files:**
- Modify: `commander/src/ironclaude/orchestrator_mcp.py` (outcome lists :4076-4078 and :7437-7439; resolutions descriptions :4067-4068 and :7431-7432)
- Modify: `commander/src/brain/rules/workflow.md` (:274, :278 resolutions schema, :284 outcomes)
- Modify: `README.md` (:189 outcomes)
- Test: `commander/tests/test_orchestrator_mcp.py` (only if it asserts the outcome-list text)

**Depends on:** Task 5 (`target-moved` must exist in code first).

**Step 1 (RED-or-none):** If `test_orchestrator_mcp.py` asserts the outcome docstring set, extend it to require `target-moved` (RED) and run:
```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_orchestrator_mcp.py -q
```
Otherwise document "No tests required: pure docstring/markdown documentation (no behavioral assertion for the outcome-list text)."

**Step 2 (GREEN):**
- orchestrator_mcp.py: add `target-moved` to both outcome lists (:4078, :7439); in both resolutions descriptions (:4067-4068, :7431-7432) note each entry may carry `category` (the surfaced `[…]` tag) so a consented reap `--force`s only when consent covered a `dirty` worktree.
- workflow.md:
  - :274/:278 — instruct the Brain to set `category` in a resolution ONLY from a tag/quoted surfaced label present in the operator's message (e.g. `reap ab12cd34 [dirty]`); never infer it; omit it otherwise. Add optional `category` to the :278 resolutions schema example.
  - :284 — revise `refused-changed` to "tip changed since surfacing OR uncommitted work the consent did not cover; for a `[dirty]` entry re-consent naming `dirty`, else re-surface", and add `target-moved` (target advanced concurrently — retry the same resolution).
- README.md :189 — mirror: add `target-moved`, the revised `refused-changed`, and the per-resolution `category`.

**Step 3 (verify):**
```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_orchestrator_mcp.py -q
```
Expected: 0 failed.

**Step 4 (stage):**
```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/orchestrator_mcp.py commander/src/brain/rules/workflow.md README.md commander/tests/test_orchestrator_mcp.py
```

---

## Final verification (after all tasks)

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npx vitest run
```
```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_orchestrator_mcp.py tests/test_worktree_reaper.py tests/test_notifications.py tests/test_workspace_client.py -q
```
Expected: all green.
