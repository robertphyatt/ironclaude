# Reaper Target Fallback Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Make `reapAmbiguousOrphans` judge orphans against the branch `origin/HEAD` points
to when it is set, and otherwise against the primary checkout's current branch (the
v1.1.12 behavior; a detached HEAD fails safe). Leave `canonicalDefaultBranchRef` and
merge-then-reap unchanged.

**Requirements:** docs/plans/2026-09-25-default-branch-fallback-requirements.md

**Design:** docs/plans/2026-09-25-default-branch-fallback-design.md

**Architecture:**
- A new helper, `originHeadBranchRef(cwd): string | null`, in `git.ts`.
  `canonicalDefaultBranchRef` is refactored onto it with no behavior change.
- The reaper target at `workspace-service.ts:990` becomes
  `originHeadBranchRef(p) ?? integrationTargetRef(primaryBranch(p))`.
- `mergeOrphanThenReap` (:1357) is untouched and keeps failing closed.

**Tech Stack:** TypeScript, vitest, esbuild bundle (`npm run build` = `tsc && npm run bundle`).

## Grounding (verified against live source while planning)

- `worker/mcp-servers/workspace-manager/src/git.ts`:
  - `primaryBranch(primaryCheckoutPath)` is at :189-200. It returns the bare name and
    throws `'Primary checkout is in detached HEAD; supply integration_target explicitly'`
    on a detached HEAD.
  - The doc comment for `canonicalDefaultBranchRef` and the function itself are at
    :202-216.
  - The module imports `spawnSync` (:1) and defines `GIT_MAX_BUFFER`.
- `src/workspace-service.ts`:
  - The `./git.js` import list is at :22-45 (alphabetical; `listWorktrees` at :37,
    `primaryBranch` at :38).
  - The module-local `function integrationTargetRef(target)` is at :235.
  - The reaper doc comment is at :976-984, with "ancestor of the primary branch" at
    :981-982.
  - The reaper target is at :990. It is computed before any mutation, including the
    `orphan_surface` prune at :1011.
- `src/__tests__/workspace-service.test.ts`:
  - The import at :9 is `import { canonicalDefaultBranchRef, ensureManagedWorktreeExclusion, gitSupportsMergeTreeWriteTree, worktreeIsClean } from '../git.js';`.
  - `describe('canonicalDefaultBranchRef')` is at :1629-1642.
  - Inside `describe('reapAmbiguousOrphans')` (from :1644) are the helpers
    `orphanBranch` / `createOrphanWorktree` / `commitFile` (:1645-1664) and titles
    containing "primary branch" at :1666, :1692 and :1748.
  - The trunk squash-merge test is at :2141-2168, with its `origin/HEAD` setup at
    :2150-2152. `directories` and `service()` are in scope there.
  - The `:2966` merge-then-reap fail-closed test stays unmodified.
- The daemon counts a per-repo reaper exception as `repo_failures`
  (`commander/src/ironclaude/main.py:571`).

## Execution invariants

- Bash cwd is `/Users/roberthyatt/Code/ironclaude/commander`. Every command uses absolute
  paths or `git -C /Users/roberthyatt/Code/ironclaude`.
- Shell state does not persist between steps.
- `docs/` and `dist/` need `git add -f`.

---

## Task 1: `originHeadBranchRef` and the reaper target fallback, with tests and doc wording

**Files:**
- Modify: `worker/mcp-servers/workspace-manager/src/git.ts:202-216`
- Modify: `worker/mcp-servers/workspace-manager/src/workspace-service.ts` (import list :22-45, doc :981-982, target :990)
- Test: `worker/mcp-servers/workspace-manager/src/__tests__/workspace-service.test.ts`

**Step 1 (RED): Edit the tests.**

(a) Import at :9. Change it to:

```ts
import { canonicalDefaultBranchRef, ensureManagedWorktreeExclusion, gitSupportsMergeTreeWriteTree, originHeadBranchRef, worktreeIsClean } from '../git.js';
```

(b) Insert this new block immediately **after** the closing `});` of
`describe('canonicalDefaultBranchRef')` (:1642). The existing `canonicalDefaultBranchRef`
tests stay unchanged.

```ts
  describe('originHeadBranchRef', () => {
    it('returns refs/heads/<name> when origin/HEAD symbolically resolves to refs/remotes/origin/<name>', () => {
      const root = repository();
      git(root, 'symbolic-ref', 'refs/remotes/origin/HEAD', 'refs/remotes/origin/trunk');

      expect(originHeadBranchRef(root)).toBe('refs/heads/trunk');
    });

    it('returns null when origin/HEAD is unset', () => {
      const root = repository();

      expect(originHeadBranchRef(root)).toBeNull();
    });
  });
```

(c) Change these test titles; the test bodies do not change:
- :1666 `'reaps a clean orphan worktree whose branch tip is an ancestor of the primary branch under ttlHours:0'`
  → `'reaps a clean orphan worktree whose branch tip is an ancestor of the reaper target under ttlHours:0'`
- :1692 `'preserves an orphan worktree whose branch has a commit not on the primary branch'`
  → `'preserves an orphan worktree whose branch has a commit not on the reaper target'`
- :1748 `'deletes a dangling branch with no worktree when it is an ancestor of the primary branch'`
  → `'deletes a dangling branch with no worktree when it is an ancestor of the reaper target'`

(d) In the trunk squash-merge test (:2141), delete these three lines (:2150-2152):

```ts
      const trunkTip = git(directory, 'rev-parse', 'HEAD');
      git(directory, 'update-ref', 'refs/remotes/origin/trunk', trunkTip);
      git(directory, 'symbolic-ref', 'refs/remotes/origin/HEAD', 'refs/remotes/origin/trunk');
```

(e) Insert these two tests directly after the trunk squash-merge test's closing `});`
(after the line `expect(detail?.evidence).toContain('refs/heads/trunk');` and its `});`),
inside `describe('reapAmbiguousOrphans')`:

```ts
    it('reaps a merged orphan against the primary checkout branch on a master repository with no origin/HEAD', () => {
      const directory = mkdtempSync(join(tmpdir(), 'ironclaude-orphan-master-'));
      directories.push(directory);
      git(directory, 'init', '--initial-branch=master');
      git(directory, 'config', 'user.name', 'Workspace Test');
      git(directory, 'config', 'user.email', 'workspace-test@example.invalid');
      writeFileSync(join(directory, 'README.md'), 'initial\n');
      git(directory, 'add', 'README.md');
      git(directory, 'commit', '-m', 'initial');
      const manager = service(directory);
      const guid = randomUUID();
      const worktreePath = createOrphanWorktree(directory, guid);

      const result = manager.reapAmbiguousOrphans({ repositoryPath: directory, ttlHours: 0 });

      expect(result.preservedUnmerged).toEqual([]);
      expect(result.reaped).toEqual([orphanBranch(guid)]);
      expect(existsSync(worktreePath)).toBe(false);
    });

    it('fails safe (throws, reaps nothing) when the primary checkout is detached and origin/HEAD is unset', () => {
      const root = repository();
      const manager = service(root);
      const guid = randomUUID();
      const worktreePath = createOrphanWorktree(root, guid);
      git(root, 'checkout', '-q', '--detach');

      expect(() => manager.reapAmbiguousOrphans({ repositoryPath: root, ttlHours: 0 })).toThrow(/detached HEAD/);
      expect(existsSync(worktreePath)).toBe(true);
      expect(git(root, 'branch', '--list', orphanBranch(guid))).not.toBe('');
    });
```

**Step 2: Run the tests and confirm RED.**

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npx vitest run src/__tests__/workspace-service.test.ts
```

Expected: RED, with failures limited to the new and changed tests.
- `originHeadBranchRef` is not yet exported. Either the two `originHeadBranchRef` tests
  fail (`originHeadBranchRef is not a function`), or the file fails to load with an error
  that names `originHeadBranchRef`.
- If the file loads, these also fail:
  - the trunk squash-merge test (`:2141`);
  - the new `master` reaper test (the orphan is preserved as unmerged, not reaped);
  - the new detached test (no throw).
- No other test fails.

**Step 3 (GREEN): Implement in `src/git.ts`.** Replace :202-216 (the doc comment and
`canonicalDefaultBranchRef`) with:

```ts
/**
 * The branch `refs/remotes/origin/HEAD` names, as ref `refs/heads/<name>`, or
 * null when origin/HEAD is unset, unresolvable, or does not point under
 * `refs/remotes/origin/`. Never throws.
 */
export function originHeadBranchRef(cwd: string): string | null {
  const result = spawnSync('git', ['-C', cwd, 'symbolic-ref', '--quiet', 'refs/remotes/origin/HEAD'], { encoding: 'utf8', maxBuffer: GIT_MAX_BUFFER });
  if (result.error || result.status !== 0) return null;
  const ref = (result.stdout || '').trim();
  const prefix = 'refs/remotes/origin/';
  if (!ref.startsWith(prefix)) return null;
  return `refs/heads/${ref.slice(prefix.length)}`;
}

/**
 * The repository's canonical default branch, as ref `refs/heads/<name>`,
 * derived from `refs/remotes/origin/HEAD` — never the primary checkout's
 * live current branch, which an operator may have moved. Falls back to
 * `refs/heads/main` whenever origin/HEAD is unset, unresolvable, or does not
 * point under `refs/remotes/origin/`. Never throws.
 */
export function canonicalDefaultBranchRef(cwd: string): string {
  return originHeadBranchRef(cwd) ?? 'refs/heads/main';
}
```

**Step 4: Implement in `src/workspace-service.ts`.**

(a) Import list (:22-45). Add `originHeadBranchRef,` on its own line between
`listWorktrees,` and `primaryBranch,`.

(b) Doc comment (:981-982). Change `ancestor of the primary branch is actually removed.`
to `ancestor of the reaper target (origin/HEAD's default branch, else the primary checkout's branch) is actually removed.`

(c) Target (:990). Replace
`    const target = canonicalDefaultBranchRef(repository.primaryCheckoutPath);`
with:

```ts
    // origin/HEAD's branch when set; otherwise the primary checkout's current
    // branch (v1.1.12 behavior — a master/trunk repository without origin/HEAD
    // must not be judged against a nonexistent refs/heads/main). A detached
    // primary makes primaryBranch throw: the sweep fails safe, reaping nothing.
    // The reaper never advances a ref, unlike mergeOrphanThenReap, which keeps
    // canonicalDefaultBranchRef's fail-closed default.
    const target = originHeadBranchRef(repository.primaryCheckoutPath)
      ?? integrationTargetRef(primaryBranch(repository.primaryCheckoutPath));
```

**Step 5: Run the tests and confirm GREEN.**

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npx vitest run src/__tests__/workspace-service.test.ts
```

Expected: 0 failed. This includes the unmodified `:2966` fail-closed test and the
existing `canonicalDefaultBranchRef` tests.

**Step 6: Confirm the old wording is gone and merge-then-reap is untouched.**

```bash
rg -n -F 'the primary branch' /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/src/workspace-service.ts
```

Expected: no output (rg exits 1). While planning, this matched only :982.

```bash
rg -n -F ': canonicalDefaultBranchRef(primary);' /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/src/workspace-service.ts
```

Expected: exactly one match. This is the unchanged `mergeOrphanThenReap` default.

**Step 7: Stage the changes.**

```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/mcp-servers/workspace-manager/src/git.ts worker/mcp-servers/workspace-manager/src/workspace-service.ts worker/mcp-servers/workspace-manager/src/__tests__/workspace-service.test.ts
```

Expected: all three files are staged.

---

## Task 2: dist rebuild, full vitest, CHANGELOG entry

No tests are required for this task: it is a build, a documentation entry, and full-suite
verification. The behavior is tested in Task 1.

**Files:**
- Modify: `worker/mcp-servers/workspace-manager/dist/cli.js`, `dist/index.js`, `dist/hook-intent.js` (build output)
- Modify: `CHANGELOG.md` (`## [Unreleased]`)

**Step 1: Rebuild.**

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npm run build
```

Expected: `tsc` and the bundle both succeed, with no error.

**Step 2: Confirm the new helper is in the bundle.**

```bash
rg -n -F 'function originHeadBranchRef(' /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/dist/cli.js
```

Expected: exactly one match. The bundle keeps function names, as the existing
`function primaryBranch` there shows.

**Step 3: Run the full workspace-manager suite.**

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npx vitest run
```

Expected: 0 failed. Ignore the benign `onTaskUpdate` RPC-timeout line.

**Step 4: Add the CHANGELOG entry.** Insert this bullet under `## [Unreleased]`, directly
after the existing first bullet (the finalize-failure observability entry), with one blank
line before and after it:

```markdown
- **Worktree reaper judges orphan merged-ness against the repository's default branch, not whatever branch the primary checkout happens to be on.** `reapAmbiguousOrphans` used the primary checkout's checked-out branch as its ancestry target, so an operator sitting on a feature branch could get a feature-only orphan reaped (or a `main`-merged one preserved). It now targets the branch `refs/remotes/origin/HEAD` names (new `originHeadBranchRef`); when origin/HEAD is unset it keeps the previous target — the primary checkout's current branch — so a `master`/`trunk` repository without origin/HEAD is never judged against a nonexistent `main`, and a detached primary still fails that repository's sweep safely (nothing reaped). The reaper never advances a ref; orphan merge-then-reap is unchanged and still fails closed when its default target (origin/HEAD's branch, else `main`) does not resolve. (`git.ts`, `workspace-service.ts`, rebuilt `dist/`; `workspace-service.test.ts`.) Deploy: refresh the plugin-cache workspace-manager `dist/`.
```

**Step 5: Stage the changes.**

```bash
git -C /Users/roberthyatt/Code/ironclaude add -f worker/mcp-servers/workspace-manager/dist/cli.js worker/mcp-servers/workspace-manager/dist/index.js worker/mcp-servers/workspace-manager/dist/hook-intent.js CHANGELOG.md commander/docs/plans/2026-09-25-default-branch-fallback.md commander/docs/plans/2026-09-25-default-branch-fallback.plan.json
```

Expected: all files are staged. A dist file with no byte change is a no-op.
