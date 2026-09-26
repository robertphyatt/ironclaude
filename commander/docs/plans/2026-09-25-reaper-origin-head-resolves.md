# Reaper: Adopt origin/HEAD Only When Its Local Branch Exists — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Make `reapAmbiguousOrphans` use `origin/HEAD`'s branch only when that local
branch exists, and otherwise use the primary checkout's branch. A `clone -b` checkout or
a stale `origin/HEAD` is then never judged against a missing ref.

**Requirements:** docs/plans/2026-09-25-reaper-origin-head-resolves-requirements.md

**Design:** docs/plans/2026-09-25-reaper-origin-head-resolves-design.md

**Architecture:** The reaper's target selection (`workspace-service.ts:997-998`) gains a
`this.refResolves` check on the `origin/HEAD`-derived ref. `mergeOrphanThenReap` and
`canonicalDefaultBranchRef` do not change. Only doc comments change in `git.ts`.

**Tech Stack:** TypeScript, vitest, esbuild bundle.

## Grounding (verified against live source while planning)

- `src/workspace-service.ts`:
  - `private refResolves(root: string, ref: string): boolean` is at :749-756. It runs
    `rev-parse --verify --quiet`.
  - The reaper doc comment is at :977-985, and its over-long line is :983.
  - The target comment is at :991-996 and the target at :997-998:
    ```ts
        const target = originHeadBranchRef(repository.primaryCheckoutPath)
          ?? integrationTargetRef(primaryBranch(repository.primaryCheckoutPath));
    ```
- `src/git.ts`:
  - The `originHeadBranchRef` doc is at :202-206 and the function at :207-214.
  - The `canonicalDefaultBranchRef` doc is at :216-222. Both docs say "unset,
    unresolvable, or does not point under".
- `src/__tests__/workspace-service.test.ts`:
  - `git(cwd, ...args)` (:37-39) returns trimmed stdout.
  - The detached fail-safe test is at :2202-2212. The next test starts at :2214:
    `it('preserves a clean orphan that IS an ancestor of the primary\'s current (non-default) branch ...`.
  - `repository()`, `service()`, `directories`, `createOrphanWorktree` and `orphanBranch`
    are all in scope inside `describe('reapAmbiguousOrphans')`.
- `CHANGELOG.md:17` holds the reaper bullet, which contains the phrase
  `It now targets the branch \`refs/remotes/origin/HEAD\` names (new \`originHeadBranchRef\`); when origin/HEAD is unset it keeps`.

## Execution invariants

- Bash cwd is `/Users/roberthyatt/Code/ironclaude/commander`. Every command uses absolute
  paths or `git -C /Users/roberthyatt/Code/ironclaude`.
- Shell state does not persist between steps.
- `docs/` and `dist/` need `git add -f`.

---

## Task 1: Resolves check on the reaper target, with tests and doc wording

**Files:**
- Modify: `worker/mcp-servers/workspace-manager/src/workspace-service.ts` (:977-998)
- Modify: `worker/mcp-servers/workspace-manager/src/git.ts` (doc comments only, :202-206 and :216-222)
- Test: `worker/mcp-servers/workspace-manager/src/__tests__/workspace-service.test.ts`

**Step 1 (RED): Add two tests.** Insert them directly after the detached fail-safe test's
closing `});` (:2212), before the test at :2214:

```ts
    it('reaps against the primary checkout branch when origin/HEAD names a branch with no local ref (clone -b)', () => {
      const source = repository();
      git(source, 'checkout', '-q', '-b', 'develop');
      writeFileSync(join(source, 'develop.txt'), 'develop\n');
      git(source, 'add', 'develop.txt');
      git(source, 'commit', '-q', '-m', 'develop');
      git(source, 'checkout', '-q', 'main');
      const parent = mkdtempSync(join(tmpdir(), 'ironclaude-orphan-clone-'));
      directories.push(parent);
      const clone = join(parent, 'clone');
      git(parent, 'clone', '-q', '-b', 'develop', source, clone);
      expect(git(clone, 'symbolic-ref', 'refs/remotes/origin/HEAD')).toBe('refs/remotes/origin/main');
      expect(git(clone, 'branch', '--list', 'main')).toBe('');
      const manager = service(clone);
      const guid = randomUUID();
      const worktreePath = createOrphanWorktree(clone, guid);

      const result = manager.reapAmbiguousOrphans({ repositoryPath: clone, ttlHours: 0 });

      expect(result.preservedUnmerged).toEqual([]);
      expect(result.reaped).toEqual([orphanBranch(guid)]);
      expect(existsSync(worktreePath)).toBe(false);
    });

    it('reaps against the primary checkout branch when origin/HEAD is a stale symref to a missing origin branch', () => {
      const root = repository();
      git(root, 'symbolic-ref', 'refs/remotes/origin/HEAD', 'refs/remotes/origin/master');
      const manager = service(root);
      const guid = randomUUID();
      const worktreePath = createOrphanWorktree(root, guid);

      const result = manager.reapAmbiguousOrphans({ repositoryPath: root, ttlHours: 0 });

      expect(result.preservedUnmerged).toEqual([]);
      expect(result.reaped).toEqual([orphanBranch(guid)]);
      expect(existsSync(worktreePath)).toBe(false);
    });
```

**Step 2: Run the tests and confirm RED.**

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npx vitest run src/__tests__/workspace-service.test.ts
```

Expected: exactly 2 failed, the two new tests. In each, `preservedUnmerged` contains the
orphan, because the target is a missing `refs/heads/main` in the first test and
`refs/heads/master` in the second. Every other test passes.

**Step 3 (GREEN): Implement in `src/workspace-service.ts`.**

(a) Replace the target comment and target (:991-998) with:

```ts
    // origin/HEAD's branch when set AND present locally; otherwise the primary
    // checkout's current branch (v1.1.12 behavior). origin/HEAD can name a
    // branch with no local ref — a `clone -b` checkout, or a stale origin/HEAD
    // after a remote default-branch rename — and judging against a missing ref
    // would misread every orphan as unmerged. A detached primary makes
    // primaryBranch throw: the sweep fails safe, reaping nothing. The reaper
    // never advances a ref, unlike mergeOrphanThenReap, which keeps
    // canonicalDefaultBranchRef's fail-closed default.
    const originHead = originHeadBranchRef(repository.primaryCheckoutPath);
    const target = originHead && this.refResolves(repository.primaryCheckoutPath, originHead)
      ? originHead
      : integrationTargetRef(primaryBranch(repository.primaryCheckoutPath));
```

(b) Re-wrap the doc comment at :981-984. Replace these four lines:

```
   * it. Every disposition preserves work by default: only a worktree proven
   * clean, old enough (`ttlHours`), unprotected, and whose branch tip is an
   * ancestor of the reaper target (origin/HEAD's default branch, else the primary checkout's branch) is actually removed. A dangling branch
   * with no worktree at all is reaped the same way, by branch tip alone.
```

with:

```
   * it. Every disposition preserves work by default: only a worktree proven
   * clean, old enough (`ttlHours`), unprotected, and whose branch tip is an
   * ancestor of the reaper target (origin/HEAD's default branch when it exists
   * locally, else the primary checkout's branch) is actually removed. A
   * dangling branch with no worktree at all is reaped the same way, by branch
   * tip alone.
```

**Step 4: Fix the doc wording in `src/git.ts`. Only comments change.**

(a) `originHeadBranchRef` doc (:202-206). Replace it with:

```ts
/**
 * The branch `refs/remotes/origin/HEAD` names, as ref `refs/heads/<name>`, or
 * null when origin/HEAD is unset or does not point under
 * `refs/remotes/origin/`. The returned LOCAL ref may not exist — a
 * `clone -b` checkout, or a stale origin/HEAD after a remote default-branch
 * rename — so callers must verify it before judging against it. Never throws.
 */
```

(b) `canonicalDefaultBranchRef` doc (:216-222). Replace it with:

```ts
/**
 * The repository's canonical default branch, as ref `refs/heads/<name>`,
 * derived from `refs/remotes/origin/HEAD` — never the primary checkout's
 * live current branch, which an operator may have moved. Falls back to
 * `refs/heads/main` whenever origin/HEAD is unset or does not point under
 * `refs/remotes/origin/`. The returned ref may not exist locally; callers
 * that advance it fail closed on an unresolvable target. Never throws.
 */
```

**Step 5: Run the tests and confirm GREEN.**

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npx vitest run src/__tests__/workspace-service.test.ts
```

Expected: 0 failed.

**Step 6: Wording guards.**

```bash
rg -n -F 'unset, unresolvable' /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/src/git.ts
```

Expected: no output (rg exits 1). While planning, this matched exactly :204 and :220, the
two doc comments.

```bash
rg -n -F ': canonicalDefaultBranchRef(primary);' /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/src/workspace-service.ts
```

Expected: exactly one match. This is the unchanged merge-then-reap default.

**Step 7: Stage the changes.**

```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/mcp-servers/workspace-manager/src/git.ts worker/mcp-servers/workspace-manager/src/workspace-service.ts worker/mcp-servers/workspace-manager/src/__tests__/workspace-service.test.ts
```

Expected: all three files are staged.

---

## Task 2: dist rebuild, full vitest, CHANGELOG wording

No tests are required for this task: it is a build, a documentation edit, and full-suite
verification. The behavior is tested in Task 1.

**Files:**
- Modify: `worker/mcp-servers/workspace-manager/dist/cli.js`, `dist/index.js`, `dist/hook-intent.js` (build output)
- Modify: `CHANGELOG.md:17`

**Step 1: Rebuild.**

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npm run build
```

Expected: `tsc` and the bundle both succeed, with no error.

**Step 2: Confirm the resolves check is in both bundles.**

```bash
rg -n -F 'originHead && this.refResolves(' /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/dist/cli.js /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/dist/index.js
```

Expected: one match in `dist/cli.js` and one in `dist/index.js`.

**Step 3: Run the full workspace-manager suite.**

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npx vitest run
```

Expected: 0 failed. Ignore the benign `onTaskUpdate` RPC-timeout line.

**Step 4: Update the CHANGELOG wording.** On `CHANGELOG.md:17`, replace the exact text

`It now targets the branch \`refs/remotes/origin/HEAD\` names (new \`originHeadBranchRef\`); when origin/HEAD is unset it keeps`

with

`It now targets the branch \`refs/remotes/origin/HEAD\` names (new \`originHeadBranchRef\`) when that branch exists locally; when origin/HEAD is unset, or names a branch with no local ref (a \`clone -b\` checkout, or a stale origin/HEAD after a remote default-branch rename), it keeps`

(The backslashes above only escape the backticks inside this inline code; the file itself
contains plain backticks.)

**Step 5: Stage the changes.**

```bash
git -C /Users/roberthyatt/Code/ironclaude add -f worker/mcp-servers/workspace-manager/dist/cli.js worker/mcp-servers/workspace-manager/dist/index.js worker/mcp-servers/workspace-manager/dist/hook-intent.js CHANGELOG.md commander/docs/plans/2026-09-25-reaper-origin-head-resolves.md commander/docs/plans/2026-09-25-reaper-origin-head-resolves.plan.json
```

Expected: all files are staged. A dist file with no byte change is a no-op.
