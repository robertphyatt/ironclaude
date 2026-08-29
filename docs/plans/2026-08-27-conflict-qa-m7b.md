# M7b — Conflict Detect + Plain-Language Surface — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Classify a paused-conflict integration rebase per-file into the M7a taxonomy and surface
the conflicts to the operator in plain language, REPLACING the raw "unresolved conflicts remain …
Unmerged paths: …" throw — while the verb still refuses/pauses (no apply, no landing change).

**Requirements:** docs/plans/2026-08-27-conflict-qa-m7b-requirements.md

**Design:** docs/plans/2026-08-27-conflict-qa-m7a-design.md (M7b = the detect+surface slice)

**Architecture:** Add a READ-ONLY `classifyRebaseConflicts(worktree)` that maps each unmerged path's
`git status --porcelain` conflict code to a class + a two-sided plain-language summary, and a
`conflicts?` field on `FinalizationResult`. Then convert `recoverRebaseInProgress`'s two
conflict-specific throws into a structured `rebase-paused-conflict` return carrying `conflicts`.
Outcome is unchanged (refuse/pause, no integration); only the surface changes from a raw git error to
operator-legible conflicts. No apply, no fresh-commit authority, no AskUserQuestion (all M7c).

**Tech Stack:** TypeScript, vitest, git.

**Execution invariants:** absolute paths; `docs/` gitignored (`git add -f`); vitest
`--testTimeout=30000`; each task's full-suite run is its falsifier; distinctive `-t` names free of
regex metachars (no parentheses in titles) so a `-t` filter matches; baseline 291 green; Bash cwd
persists between calls — `cd` to the workspace-manager dir once for `npx tsc`, use
`git -C /Users/roberthyatt/Code/ironclaude` for staging.

---

## Task 1: `classifyRebaseConflicts` + the `conflicts` payload

**Files:**
- Modify: `worker/mcp-servers/workspace-manager/src/integration.ts` (`FinalizationResult` :36-52; add the classifier near `classifyRebaseState` :1616)
- Test: `worker/mcp-servers/workspace-manager/src/__tests__/integration-cases.ts`

Read-only detection engine; wires into no path yet (Task 2 does). git's `status --porcelain` XY code
for an unmerged path is the standard conflict-type signal: `UU`=both-modified (overlap), `AA`=both-added,
`UD`=modified-by-us/deleted-by-them, `DU`=deleted-by-us/modified-by-them; binary is detected via
`git diff --numstat -- <path>` returning `-\t-` (binary precedence over the XY code).

**Step 1 — RED (six classifier tests, one per taxonomy class + empty):** In `integration-cases.ts`,
each conflict test seeds a paused conflicted rebase by mirroring `seedConflictMidRebase` (:182-194)
— advance root `main` with a target-side change, stage the worktree-side change, then
`expect(() => finalizeCommanderLocalCommit(database, commanderInput(root, assignment, 'conflict'))).toThrow()`
which leaves the paused conflicted rebase in the worktree. Import `classifyRebaseConflicts` from
`../integration.js`.
- `it('classifyRebaseConflicts classifies an overlapping edit conflict as overlap', …)`: use
  `seedConflictMidRebase()` (README.md both-modified) → `classifyRebaseConflicts(s.assignment.worktree_path)`
  → one entry `{ path:'README.md', conflictClass:'overlap' }`, non-empty `summary`.
- `it('classifyRebaseConflicts classifies a delete-modify conflict without crashing', …)` **(the
  crash falsifier for the stage guard):** target commits `README.md`='target\n' (as :184-189);
  worktree stages a DELETE instead of an edit — `git(wt,'rm','README.md')` — then the failing
  finalize leaves a delete/modify paused conflict. Assert one entry `path:'README.md'`,
  `conflictClass:'delete-modify'` (do NOT pin UD vs DU), non-empty `summary`, and that the call did
  NOT throw. (Unguarded `git show :3:README.md` — the absent stage — throws here.)
- `it('classifyRebaseConflicts classifies an add-add conflict as add-add', …)`: target commits a NEW
  file `NEW.md`='target\n'; worktree writes+stages `NEW.md`='source\n' → add/add. Assert
  `conflictClass:'add-add'`.
- `it('classifyRebaseConflicts classifies a differing-binary conflict as binary', …)`: both sides add
  the same NEW path with DIFFERING binary blobs (`writeFileSync(p, Buffer.from([0,1,2,3,…]))` vs a
  different byte run) → `numstat` yields `-\t-` → assert `conflictClass:'binary'` (binary precedence
  over the underlying AA code).
- `it('classifyRebaseConflicts surfaces a rename-modify conflict with a legal class', …)`: worktree
  `git(wt,'mv','README.md','RENAMED.md')` + edit; target modifies `README.md`. Git's rename detection
  fixes the porcelain shape, so assert only: at least one entry surfaced, its `conflictClass` is a
  member of the taxonomy union, non-empty `summary`, and the classifier did NOT throw. (Do not
  over-pin git's rename heuristics.)
- `it('classifyRebaseConflicts returns an empty list for a clean worktree', …)`: a fresh
  `setup(false)` worktree (no rebase) → `[]`.

Run:
```bash
npm --prefix /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager test -- --testTimeout=30000 -t "classifyRebaseConflicts"
```
Expected: FAIL — `classifyRebaseConflicts` is not exported yet.

**Step 2 — GREEN:** In `integration.ts` add the `conflicts?` field to `FinalizationResult` (after
`detail?` :51):
```ts
  /** M7b: classified paused-rebase conflicts, surfaced in plain language (no apply — M7c). */
  conflicts?: Array<{ path: string; conflictClass: 'overlap' | 'add-add' | 'delete-modify' | 'binary' | 'other'; summary: string }>;
```
Add the exported READ-ONLY classifier near `classifyRebaseState`:
```ts
/** READ-ONLY: classify each unmerged path of a paused-conflict rebase into the M7a taxonomy
 *  with a plain-language two-sided summary. Never mutates the worktree. */
export function classifyRebaseConflicts(worktree: string): NonNullable<FinalizationResult['conflicts']> {
  const unmerged = runGit(worktree, ['diff', '--name-only', '--diff-filter=U']).trim();
  if (unmerged === '') return [];
  return unmerged.split('\n').map((path) => {
    const xy = runGit(worktree, ['status', '--porcelain=v1', '--', path]).slice(0, 2);
    const binary = /^-\t-\t/.test(runGit(worktree, ['diff', '--numstat', '--', path]).trim());
    const conflictClass = binary ? 'binary'
      : xy === 'UU' ? 'overlap'
      : xy === 'AA' ? 'add-add'
      : (xy === 'UD' || xy === 'DU') ? 'delete-modify'
      : 'other';
    // A merge stage is ABSENT when that side deleted the path (delete/modify): guard so the
    // read-only summary never throws (that would crash on a taxonomy-required class).
    const stageLines = (stage: 2 | 3): number => {
      try { return runGit(worktree, ['show', `:${stage}:${path}`]).split('\n').length; }
      catch { return 0; }
    };
    const ours = stageLines(2);
    const theirs = stageLines(3);
    const summary = `${path}: your reviewed work has ${ours} line(s) here; the integration target has ${theirs} line(s) (${conflictClass}).`;
    return { path, conflictClass, summary };
  });
}
```

**Step 3 — run suite:**
```bash
npm --prefix /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager test -- --testTimeout=30000
```
Expected: all pass (baseline 291 + the 6 new classifier tests; no path wired yet).

**Step 4 — typecheck + stage:**
```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npx tsc --noEmit && git -C /Users/roberthyatt/Code/ironclaude add worker/mcp-servers/workspace-manager/src/integration.ts worker/mcp-servers/workspace-manager/src/__tests__/integration-cases.ts
```
Expected: tsc exit 0; staged.

---

## Task 2: surface the conflicts (throw → structured return) + rewrite the STOP test

**Depends on:** Task 1. **Files:**
- Modify: `worker/mcp-servers/workspace-manager/src/integration.ts` (`recoverRebaseInProgress` ~:1570-1585)
- Test: `worker/mcp-servers/workspace-manager/src/__tests__/integration-cases.ts` (rewrite the :2135 test; add the class-1 empty-conflicts guard at the :2119 test)

`recoverRebaseInProgress`'s two conflict-specific throws become structured `rebase-paused-conflict`
returns carrying `conflicts`, with **distinct** `detail` strings so a test can tell the initial-stop
guard from the re-conflict path. The outcome is unchanged (no integration, rebase left in progress).

**Step 1 — RED (rewrite the STOP test; add the class-1 guard):** In `integration-cases.ts`:
- REWRITE `it('managed rebase recovery: unresolved conflicts STOP and do not advance the target (case B)', …)`
  (:2135): replace the two `expect(() => reconcileFinalization(… 'continue')).toThrow(…)` assertions with:
  ```ts
  // The 'unresolved conflicts remain' detail proves the op STOPPED BEFORE ever running
  // `rebase --continue` — the re-conflict-during-continue path carries a DIFFERENT detail
  // ('continuing re-conflicted'), so asserting this exact substring (not merely truthy) falsifies
  // the no-auto-resolve guard: deleting it would route to the re-conflict return and fail here.
  const result = reconcileFinalization(s.database, {
    repositoryPath: s.root, workspaceGuid: s.assignment.workspace_guid, providerRootSessionId: OWNER,
    rebaseRecovery: 'continue',
  });
  expect(result.state).toBe('rebase-paused-conflict');
  expect(result.conflicts).toEqual(expect.arrayContaining([
    expect.objectContaining({ path: 'README.md', conflictClass: 'overlap' }),
  ]));
  expect(result.detail).toContain('unresolved conflicts remain');
  ```
  Keep the existing post-assertions (target unmoved :2153, rebase still in progress :2155, row ready
  :2158) — they still hold (no integration).
- In the conflict-free auto-integrate (class-1) test at :2119 ('managed rebase recovery: a clean/no
  content-collision resolution auto-continues …', which asserts `result.state==='cleaned'` at :2127),
  ADD `expect(result.conflicts).toBeUndefined();` — HC4 regression guard proving the proven class-1
  lane never grows a conflicts surface.

Run `-t "unresolved conflicts STOP"` → Expected: FAIL — today `continue` THROWS, so
`reconcileFinalization(...)` throws instead of returning.

**Step 2 — GREEN (throw → structured return, distinct details):** In `recoverRebaseInProgress`,
replace the initial unresolved-conflict throw (~:1572-1574):
```ts
  const unresolved = runGit(worktree, ['diff', '--name-only', '--diff-filter=U']).trim();
  if (unresolved !== '') {
    return {
      state: 'rebase-paused-conflict',
      conflicts: classifyRebaseConflicts(worktree),
      detail: 'Close-out/reconcile paused: unresolved conflicts remain; automated resolution pending (M7c). Worktree preserved; nothing integrated. Not an operator task.',
    };
  }
```
and the re-conflict-after-continue throw (~:1580-1582) likewise:
```ts
    const reconflict = runGit(worktree, ['diff', '--name-only', '--diff-filter=U']).trim();
    if (reconflict !== '') {
      return {
        state: 'rebase-paused-conflict',
        conflicts: classifyRebaseConflicts(worktree),
        detail: 'Close-out/reconcile paused: continuing re-conflicted; automated resolution pending (M7c). Worktree preserved; nothing integrated. Not an operator task.',
      };
    }
```
Leave the trailing `throw error;` and the "still in progress after continue" throw UNCHANGED (they
are not the unmerged-paths surface).

**Step 3 — run suite:**
```bash
npm --prefix /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager test -- --testTimeout=30000
```
Expected: all pass. The rewritten STOP test and the class-1 guard pass; the close-out conflict test
(:558, status-probe path) and the case-D repair-required test (:2179, already a return) still pass;
no lane integrates a conflict.

**Step 4 — typecheck + stage:**
```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npx tsc --noEmit && git -C /Users/roberthyatt/Code/ironclaude add worker/mcp-servers/workspace-manager/src/integration.ts worker/mcp-servers/workspace-manager/src/__tests__/integration-cases.ts
```
Expected: tsc exit 0; staged.
