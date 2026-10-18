# Commit Scoping for Absolute allowed_files Implementation Plan (v1.1.16 L2c)

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Make `buildScopedStagedTree` resolve absolute `allowed_files` entries instead of refusing the whole commit. An entry inside the repository is scoped by its repo-relative path. An entry outside it, or naming the root itself, is skipped. Relative entries behave as before. Then rebuild, verify and redeploy.

**Requirements:** `docs/plans/2026-10-03-commit-scoping-absolute-requirements.md`

**Design:** `docs/plans/2026-10-03-commit-scoping-absolute-design.md`

**Loop:** full. This loop changes the workspace-manager commit path.

**Architecture:**
- `scoped-tree.ts` gains `repoRelativeFromAbsolute(realRoot, entry)`. It takes the real path (`fs.realpathSync.native`) of the entry's longest existing ancestor directory and appends the rest of the entry as spelled. The final component is never resolved, because git tracks a symlink as its own entry. The result is compared with the real path of `repoPath`. Strictly inside gives a POSIX repo-relative path. Equal to the root, outside it, or a resolution error gives `null`, and the caller skips the entry.
- The loop in `buildScopedStagedTree` maps absolute entries through the helper. It then runs the existing canonical check on the resulting relative path. Relative entries go straight to the existing check, unchanged.
- All three callers pass the primary checkout's toplevel as `repoPath`:
  - `git-authority.ts:354` (`observeUnassignedCommitEvidence`) and `git-authority.ts:420` (`observeUnassignedCommitAndPushEvidence`) receive `unassigned.path` (`:510`, `:513`, `:563`, `:566`).
  - `unassigned.path` is `repository.primaryCheckoutPath` (`git-authority.ts:347`).
  - `integration.ts:443` (`validateExactCommitState`) receives `authority.worktreePath`, which is the same `unassigned.path` (`git-authority.ts:583`).
  - `primaryCheckoutPath` is the first `git worktree list --porcelain` entry, passed through `realpathSync` (`git.ts:131`, `:164-167`).
  - None of them passes a linked worktree.

**Tech Stack:** TypeScript (tsc + esbuild bundles), vitest, git.

## Execution invariants

- Shell state does not persist between steps. Use literal absolute paths.
- Use `git -C /Users/roberthyatt/Code/ironclaude` for git commands.
- zsh has `nomatch`, so quote any glob.
- `docs/` is gitignored. Plan artifacts need `git add -f`.
- `worker/mcp-servers/*/dist/` is gitignored (`.gitignore:29`). Only three bundles are tracked: `worker/mcp-servers/workspace-manager/dist/cli.js`, `dist/index.js` and `dist/hook-intent.js` (`git ls-files`). The other `tsc` outputs in `dist/` stay untracked. Stage the three bundles by explicit path.
- Do not append `2>/dev/null` to any command whose output is evidence.
- An `expected:` labeled "predicted" was not measured at planning time, because Bash was limited to read-only commands outside execution. The executor reports the actual value. If the actual value contradicts the prediction, the executor stops and reports; it does not adapt.
- Every verification must be able to fail. Each RED step names the failing tests. The bundle grep returns 0 before the rebuild.
- Measured at planning time (2026-10-03, by reading files):
  - `scoped-tree.test.ts` has 7 tests (`it(` at lines 27, 49, 64, 82, 99, 117, 136).
  - `buildScopedStagedTree` is defined once in each of the three tracked bundles (`grep -c "function buildScopedStagedTree"` = 1 in each). esbuild keeps function names, so the new helper's name can be grepped after the rebuild.
  - The workspace-manager `build` script is `tsc && npm run bundle`. `bundle` runs esbuild on `src/index.ts`, `src/cli.ts` and `src/hook-intent.ts` into `dist/index.js`, `dist/cli.js` and `dist/hook-intent.js`. `test` is `vitest run`.
  - Claude Code and Codex start the server through `mcp-servers/workspace-manager/cli/mcp-server-wrapper.js`, which imports `dist/index.js` (`worker/.mcp.json:16`, `worker/.codex-plugin/plugin.json:40`). Commander runs `<plugin_root>/mcp-servers/workspace-manager/dist/cli.js` from the installed plugin cache (`commander/src/ironclaude/workspace_client.py:43`, `:94-123`).
  - Commander suite: 3460 passed (last measured run, reported by the orchestrator). Pass Bash `timeout` 600000.
- The canonical-path error message is the only one in the repo (`grep -rn "is not a canonical repo-relative path"`). The only tests that assert `/canonical/` are `scoped-tree.test.ts:145-147`.

---

## Task 1: Absolute-entry resolution in scoped-tree.ts (R1, R2)

**Files:**
- Modify: `worker/mcp-servers/workspace-manager/src/scoped-tree.ts` (full replacement below)
- Modify: `worker/mcp-servers/workspace-manager/src/__tests__/scoped-tree.test.ts` (import line `:2`, new helper after `:17`, test `:136-148` replaced, 5 tests added)
- Modify (build outputs, tracked): `worker/mcp-servers/workspace-manager/dist/cli.js`, `worker/mcp-servers/workspace-manager/dist/index.js`, `worker/mcp-servers/workspace-manager/dist/hook-intent.js`

**Step 1 (RED): in `scoped-tree.test.ts`, change the fs import (line 2).** The current line is:

```ts
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
```

Replace it with:

```ts
import { existsSync, mkdtempSync, mkdirSync, realpathSync, rmSync, statSync, symlinkSync, writeFileSync } from 'node:fs';
```

**Step 2 (RED): add a temp-dir helper.** Insert this directly after the closing `}` of `makeTempRepo` (line 17) and before the blank line that precedes `afterEach`:

```ts

function makeTempDir(): string {
  const dir = mkdtempSync(path.join(os.tmpdir(), 'ironclaude-scoped-tree-outside-'));
  tempDirs.push(dir);
  return dir;
}
```

**Step 3 (RED): replace the last test.** The current test (lines 136-148) is:

```ts
  it('throws on a non-canonical allowed_files entry (leading ./, absolute, or trailing /)', () => {
    const repo = makeTempRepo();
    writeFileSync(path.join(repo, 'a.ts'), 'export const a = 1;\n');
    execFileSync('git', ['-C', repo, 'add', 'a.ts']);
    execFileSync('git', ['-C', repo, 'commit', '-m', 'base']);
    const parentOid = execFileSync('git', ['-C', repo, 'rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();
    writeFileSync(path.join(repo, 'a.ts'), 'export const a = 2;\n');
    execFileSync('git', ['-C', repo, 'add', 'a.ts']);

    expect(() => buildScopedStagedTree(repo, parentOid, ['./a.ts'])).toThrow(/canonical/);
    expect(() => buildScopedStagedTree(repo, parentOid, ['/abs/a.ts'])).toThrow(/canonical/);
    expect(() => buildScopedStagedTree(repo, parentOid, ['dir/'])).toThrow(/canonical/);
  });
```

Replace it with the following. It is the modified test plus five new tests, and the file's closing `});` stays after it:

```ts
  it('throws on a non-canonical relative allowed_files entry and skips an absolute entry outside the repository', () => {
    const repo = makeTempRepo();
    writeFileSync(path.join(repo, 'a.ts'), 'export const a = 1;\n');
    execFileSync('git', ['-C', repo, 'add', 'a.ts']);
    execFileSync('git', ['-C', repo, 'commit', '-m', 'base']);
    const parentOid = execFileSync('git', ['-C', repo, 'rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();
    const parentTree = execFileSync('git', ['-C', repo, 'rev-parse', `${parentOid}^{tree}`], { encoding: 'utf8' }).trim();
    writeFileSync(path.join(repo, 'a.ts'), 'export const a = 2;\n');
    execFileSync('git', ['-C', repo, 'add', 'a.ts']);

    expect(() => buildScopedStagedTree(repo, parentOid, ['./a.ts'])).toThrow(/canonical/);
    expect(() => buildScopedStagedTree(repo, parentOid, ['dir/'])).toThrow(/canonical/);
    expect(() => buildScopedStagedTree(repo, parentOid, ['a/../a.ts'])).toThrow(/canonical/);
    expect(() => buildScopedStagedTree(repo, parentOid, ['a//a.ts'])).toThrow(/canonical/);
    expect(() => buildScopedStagedTree(repo, parentOid, [''])).toThrow(/canonical/);
    // Outside any repository: skipped, so the scoped tree is the parent tree.
    expect(buildScopedStagedTree(repo, parentOid, ['/abs/a.ts'])).toBe(parentTree);
  });

  it('scopes an absolute entry inside the repository by its repo-relative path', () => {
    const repo = makeTempRepo();
    const realRepo = realpathSync.native(repo);
    mkdirSync(path.join(repo, 'gone'));
    writeFileSync(path.join(repo, 'gone', 'old.txt'), 'old\n');
    writeFileSync(path.join(repo, 'base.txt'), 'base\n');
    execFileSync('git', ['-C', repo, 'add', 'base.txt', 'gone/old.txt']);
    execFileSync('git', ['-C', repo, 'commit', '-m', 'base']);
    const parentOid = execFileSync('git', ['-C', repo, 'rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();
    mkdirSync(path.join(repo, 'src'));
    writeFileSync(path.join(repo, 'src', 'a.ts'), 'export const a = 1;\n');
    writeFileSync(path.join(repo, 'foreign.ts'), 'export const foreign = 1;\n');
    execFileSync('git', ['-C', repo, 'add', 'src/a.ts', 'foreign.ts']);
    // Staged deletion whose directory no longer exists on disk.
    execFileSync('git', ['-C', repo, 'rm', '-r', '-q', 'gone']);
    const aOid = execFileSync('git', ['-C', repo, 'rev-parse', ':src/a.ts'], { encoding: 'utf8' }).trim();

    const tree = buildScopedStagedTree(repo, parentOid, [
      path.join(realRepo, 'src', 'a.ts'),
      path.join(realRepo, 'gone', 'old.txt'),
    ]);
    const listing = execFileSync('git', ['-C', repo, 'ls-tree', '-r', tree], { encoding: 'utf8' });

    expect(listing).toContain(`${aOid}\tsrc/a.ts`);
    expect(listing).not.toContain('gone/old.txt');
    expect(listing).not.toContain('foreign.ts');
    expect(listing).toContain('base.txt');
  });

  it('skips absolute entries outside the repository, or naming its root, while scoping relative siblings', () => {
    const repo = makeTempRepo();
    const realRepo = realpathSync.native(repo);
    writeFileSync(path.join(repo, 'base.txt'), 'base\n');
    execFileSync('git', ['-C', repo, 'add', 'base.txt']);
    execFileSync('git', ['-C', repo, 'commit', '-m', 'base']);
    const parentOid = execFileSync('git', ['-C', repo, 'rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();
    const outside = makeTempDir();
    writeFileSync(path.join(outside, 'side.txt'), 'side\n');
    // A directory whose path string starts with the repo root but is a sibling.
    const siblingPrefix = `${realRepo}-other`;
    mkdirSync(siblingPrefix);
    tempDirs.push(siblingPrefix);
    writeFileSync(path.join(siblingPrefix, 'c.ts'), 'export const c = 0;\n');
    writeFileSync(path.join(repo, 'a.ts'), 'export const a = 1;\n');
    writeFileSync(path.join(repo, 'b.ts'), 'export const b = 2;\n');
    writeFileSync(path.join(repo, 'c.ts'), 'export const c = 3;\n');
    execFileSync('git', ['-C', repo, 'add', 'a.ts', 'b.ts', 'c.ts']);

    const tree = buildScopedStagedTree(repo, parentOid, [
      'a.ts',
      path.join(outside, 'side.txt'),
      path.join(siblingPrefix, 'c.ts'),
      realRepo,
      '/abs/never/a.ts',
    ]);
    const names = execFileSync('git', ['-C', repo, 'ls-tree', '-r', '--name-only', tree], { encoding: 'utf8' }).split('\n');

    expect(names).toContain('a.ts');
    expect(names).toContain('base.txt');
    expect(names).not.toContain('b.ts');
    expect(names).not.toContain('c.ts');
    expect(names).not.toContain('side.txt');
  });

  it('scopes an absolute entry spelled through a symlink to the repository root', () => {
    const repo = makeTempRepo();
    writeFileSync(path.join(repo, 'base.txt'), 'base\n');
    execFileSync('git', ['-C', repo, 'add', 'base.txt']);
    execFileSync('git', ['-C', repo, 'commit', '-m', 'base']);
    const parentOid = execFileSync('git', ['-C', repo, 'rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();
    writeFileSync(path.join(repo, 'a.ts'), 'export const a = 1;\n');
    writeFileSync(path.join(repo, 'foreign.ts'), 'export const foreign = 1;\n');
    execFileSync('git', ['-C', repo, 'add', 'a.ts', 'foreign.ts']);
    const aOid = execFileSync('git', ['-C', repo, 'rev-parse', ':a.ts'], { encoding: 'utf8' }).trim();
    const link = path.join(makeTempDir(), 'repo-link');
    symlinkSync(repo, link);

    const tree = buildScopedStagedTree(repo, parentOid, [path.join(link, 'a.ts')]);
    const listing = execFileSync('git', ['-C', repo, 'ls-tree', '-r', tree], { encoding: 'utf8' });

    expect(listing).toContain(`${aOid}\ta.ts`);
    expect(listing).not.toContain('foreign.ts');
  });

  it('keeps a tracked symlink entry by its own path instead of following it', () => {
    const repo = makeTempRepo();
    const realRepo = realpathSync.native(repo);
    writeFileSync(path.join(repo, 'a.ts'), 'export const a = 1;\n');
    execFileSync('git', ['-C', repo, 'add', 'a.ts']);
    execFileSync('git', ['-C', repo, 'commit', '-m', 'base']);
    const parentOid = execFileSync('git', ['-C', repo, 'rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();
    writeFileSync(path.join(repo, 'a.ts'), 'export const a = 2;\n');
    symlinkSync('a.ts', path.join(repo, 'ln.ts'));
    execFileSync('git', ['-C', repo, 'add', 'a.ts', 'ln.ts']);
    const aOid = execFileSync('git', ['-C', repo, 'rev-parse', ':a.ts'], { encoding: 'utf8' }).trim();
    const lnOid = execFileSync('git', ['-C', repo, 'rev-parse', ':ln.ts'], { encoding: 'utf8' }).trim();

    const tree = buildScopedStagedTree(repo, parentOid, [path.join(realRepo, 'ln.ts')]);
    const listing = execFileSync('git', ['-C', repo, 'ls-tree', '-r', tree], { encoding: 'utf8' });

    expect(listing).toContain(`120000 blob ${lnOid}\tln.ts`);
    expect(listing).not.toContain(aOid);
  });

  it('scopes an absolute entry spelled with a case-variant repository root (case-insensitive volume only)', ({ skip }) => {
    const repo = makeTempRepo();
    const realRepo = realpathSync.native(repo);
    // The 'ironclaude-' mkdtemp prefix guarantees toUpperCase() changes the spelling.
    const variant = path.join(path.dirname(realRepo), path.basename(realRepo).toUpperCase());
    // Detect case-insensitivity by inode identity, not by realpath (realpath is under test).
    const insensitive = existsSync(variant)
      && statSync(variant).ino === statSync(realRepo).ino
      && statSync(variant).dev === statSync(realRepo).dev;
    if (!insensitive) skip();
    writeFileSync(path.join(repo, 'base.txt'), 'base\n');
    execFileSync('git', ['-C', repo, 'add', 'base.txt']);
    execFileSync('git', ['-C', repo, 'commit', '-m', 'base']);
    const parentOid = execFileSync('git', ['-C', repo, 'rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();
    writeFileSync(path.join(repo, 'a.ts'), 'export const a = 1;\n');
    writeFileSync(path.join(repo, 'foreign.ts'), 'export const foreign = 1;\n');
    execFileSync('git', ['-C', repo, 'add', 'a.ts', 'foreign.ts']);
    const aOid = execFileSync('git', ['-C', repo, 'rev-parse', ':a.ts'], { encoding: 'utf8' }).trim();

    const tree = buildScopedStagedTree(repo, parentOid, [path.join(variant, 'a.ts')]);
    const listing = execFileSync('git', ['-C', repo, 'ls-tree', '-r', tree], { encoding: 'utf8' });

    expect(listing).toContain(`${aOid}\ta.ts`);
    expect(listing).not.toContain('foreign.ts');
  });
```

Notes on the fixtures:
- On macOS, `os.tmpdir()` is under `/var`, which is a symlink to `/private/var`. The in-repo, root and sibling-prefix entries are built from `realpathSync.native(repo)`, so they test containment, not symlink resolution. The symlink test builds its entry through an explicit `repo-link` symlink.
- The case-variant test checks the requirements' "symlink and case spellings resolve". It depends on `realpathSync.native` returning the on-disk case on APFS, which was not measured at planning. If GREEN fails on this test, stop and report: the design's assumption would then be wrong. On a case-sensitive volume the test reports as skipped, not passed.
- `'../a.ts'` is deliberately not asserted. `path.posix.normalize('../a.ts')` returns `'../a.ts'`, so today's predicate at `scoped-tree.ts:33` does not reject it. R1 keeps relative behavior unchanged.

**Step 4: run it and see it fail**

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npx vitest run src/__tests__/scoped-tree.test.ts
```

Expected (predicted, on this Mac's case-insensitive APFS): exit 1, `6 failed | 6 passed (12)`.
- These six fail, because today's line 33 throws `/canonical/` on any absolute entry:
  - the modified test (on its `/abs/a.ts` assertion);
  - "scopes an absolute entry inside the repository…";
  - "skips absolute entries outside the repository…";
  - "scopes an absolute entry spelled through a symlink…";
  - "keeps a tracked symlink entry…";
  - "scopes an absolute entry spelled with a case-variant repository root…".
- The six unchanged tests pass.
- On a case-sensitive volume the result is `5 failed | 6 passed | 1 skipped (12)`.

**Step 5: replace `worker/mcp-servers/workspace-manager/src/scoped-tree.ts` with:**

```ts
import { realpathSync, rmSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { runGit, runGitEnv } from './git.js';

// Maps an absolute allowed_files entry onto the repository the way the write
// guard and the drift check accept it: the real path of the entry's longest
// existing ancestor directory, plus the rest of the entry as spelled. The final
// component is never resolved, because git tracks a symlink as its own entry.
// Returns the POSIX repo-relative path when the result lies strictly inside
// realRoot. Returns null when it equals realRoot, lies outside it, or cannot be
// resolved: no commit in this repository can contain such a file, so the caller
// skips the entry.
function repoRelativeFromAbsolute(realRoot: string, entry: string): string | null {
  const absolute = path.resolve(entry);
  const remainder = [path.basename(absolute)];
  let ancestor = path.dirname(absolute);
  for (;;) {
    try {
      const resolved = path.join(realpathSync.native(ancestor), ...remainder);
      if (!resolved.startsWith(realRoot + path.sep)) return null;
      return path.relative(realRoot, resolved).split(path.sep).join('/');
    } catch (error) {
      const code = (error as NodeJS.ErrnoException).code;
      if (code !== 'ENOENT' && code !== 'ENOTDIR') return null;
      const parent = path.dirname(ancestor);
      if (parent === ancestor) return null;
      remainder.unshift(path.basename(ancestor));
      ancestor = parent;
    }
  }
}

// Builds a commit tree = parentTree with ONLY the allowed paths' EXACT staged
// index entries overlaid (adds/modifies/deletes). Foreign staged entries — a
// directory that shadows an allowed file name, a glob-shaped allowed entry, or any
// multi-match — are structurally excluded (exact path key, never a git pathspec).
// An allowed path with an unmerged (conflicted) index entry fails closed. Never
// reads the working tree. Caller supplies parentOid + a trusted allowedFiles list.
// An absolute allowed entry inside the repository is scoped by its repo-relative
// path; one outside it (or naming the root itself) is skipped. A non-canonical
// relative entry still refuses the commit.
export function buildScopedStagedTree(repoPath: string, parentOid: string, allowedFiles: readonly string[]): string {
  // One whole-index read; -z emits literal paths (no core.quotePath quoting).
  // Records are "<mode> <oid> <stage>\t<path>" separated by NUL.
  const raw = runGit(repoPath, ['ls-files', '--stage', '-z']);
  const index = new Map<string, { mode: string; oid: string; stage: string }[]>();
  for (const record of raw.split('\0')) {
    if (record.length === 0) continue;
    const tab = record.indexOf('\t');
    if (tab === -1) continue;
    const [mode, oid, stage] = record.slice(0, tab).split(/\s+/);
    const p = record.slice(tab + 1);
    const list = index.get(p) ?? [];
    list.push({ mode, oid, stage });
    index.set(p, list);
  }

  const tmpIndex = path.join(os.tmpdir(), `ironclaude-scoped-index-${process.pid}-${Date.now()}`);
  const env: NodeJS.ProcessEnv = { ...process.env, GIT_INDEX_FILE: tmpIndex };
  try {
    runGitEnv(repoPath, ['read-tree', parentOid], env); // temp index = parent tree
    let realRoot: string | undefined;
    for (const entry of allowedFiles) {
      let rel = entry;
      if (path.posix.isAbsolute(entry)) {
        realRoot ??= realpathSync.native(repoPath);
        const inside = repoRelativeFromAbsolute(realRoot, entry);
        if (inside === null) continue; // outside the repository: no commit here can contain it
        rel = inside;
      }
      if (rel === '' || rel.endsWith('/') || path.posix.isAbsolute(rel) || rel !== path.posix.normalize(rel)) {
        throw new Error(`Cannot scope commit: allowed_files entry '${rel}' is not a canonical repo-relative path (no leading ./, no .., no //, no trailing slash)`);
      }
      const entries = index.get(rel);
      if (entries && entries.some((e) => e.stage !== '0')) {
        throw new Error(`Cannot scope commit: '${rel}' has an unresolved merge conflict (unmerged index entry); resolve it before committing`);
      }
      const staged = entries?.find((e) => e.stage === '0');
      if (staged) {
        runGitEnv(repoPath, ['update-index', '--add', '--cacheinfo', `${staged.mode},${staged.oid},${rel}`], env);
      } else {
        runGitEnv(repoPath, ['update-index', '--force-remove', '--', rel], env); // staged deletion or unstaged/absent
      }
    }
    return runGitEnv(repoPath, ['write-tree'], env).trim();
  } finally {
    try { rmSync(tmpIndex, { force: true }); } catch { /* best effort */ }
  }
}
```

The changes against the current file are:
- line 1 imports `realpathSync`;
- the new helper and its comment;
- three added header-comment lines;
- the loop variable is now `entry`, mapped to `rel`;
- the error text drops "no absolute path".

Every other line is unchanged.

**Step 6: run the test file again**

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npx vitest run src/__tests__/scoped-tree.test.ts
```

Expected (predicted): exit 0, `12 passed (12)`. On a case-sensitive volume: `11 passed | 1 skipped (12)`.

**Step 7: confirm the bundles do not yet contain the helper**

```bash
grep -c repoRelativeFromAbsolute /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/dist/index.js /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/dist/cli.js /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/dist/hook-intent.js
```

Expected (predicted): each file reports `:0`; exit 1.

**Step 8: rebuild**

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npm run build
```

Expected: exit 0, and `tsc` reports no type errors. esbuild writes `dist/index.js`, `dist/cli.js` and `dist/hook-intent.js`.

**Step 9: confirm the rebuilt bundles contain the helper**

```bash
grep -c repoRelativeFromAbsolute /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/dist/index.js /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/dist/cli.js /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/dist/hook-intent.js
```

Expected (predicted): each file reports a count of at least 1; exit 0.

**Step 10: run the full workspace-manager suite**

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npx vitest run
```

Expected (predicted): exit 0, `0 failed`. The total was not measured at planning. A known `onTaskUpdate` RPC-timeout log line from `integration-core.test.ts` is benign; judge the run by the failed count.

**Step 11: stage**

```bash
git -C /Users/roberthyatt/Code/ironclaude add -f worker/mcp-servers/workspace-manager/src/scoped-tree.ts worker/mcp-servers/workspace-manager/src/__tests__/scoped-tree.test.ts worker/mcp-servers/workspace-manager/dist/cli.js worker/mcp-servers/workspace-manager/dist/index.js worker/mcp-servers/workspace-manager/dist/hook-intent.js
```

Expected: no output, exit 0.

**Step 12: confirm exactly these five paths are staged for the workspace-manager**

```bash
git -C /Users/roberthyatt/Code/ironclaude diff --cached --name-only -- worker/mcp-servers/workspace-manager
```

Expected: exactly these five lines. No other `dist/` file appears.
- `worker/mcp-servers/workspace-manager/dist/cli.js`
- `worker/mcp-servers/workspace-manager/dist/hook-intent.js`
- `worker/mcp-servers/workspace-manager/dist/index.js`
- `worker/mcp-servers/workspace-manager/src/__tests__/scoped-tree.test.ts`
- `worker/mcp-servers/workspace-manager/src/scoped-tree.ts`

---

## Task 2: Verify, redeploy and stage the loop docs (R3, R4)

**Files:**
- Create: `docs/plans/2026-10-03-commit-scoping-absolute-deploy-log.md`
- Stage (no edits): `docs/plans/2026-10-03-commit-scoping-absolute-requirements.md`, `docs/plans/2026-10-03-commit-scoping-absolute-design.md`, `docs/plans/2026-10-03-commit-scoping-absolute.md` and `docs/plans/2026-10-03-commit-scoping-absolute.plan.json`. Brainstorming could not stage them. They are listed in `allowed_files` because the unassigned `/commit` lane scopes the commit tree to the union of the session's `allowed_files` (`plan-scope.ts:9-31`).

**No tests required:** this task runs the existing suites and deploys. `codex plugin add` writes a generated runtime copy outside the repo; that is install output, not an edit.

**Step 1:** run with Bash `timeout` 600000:

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q
```

Expected: `3460 passed`, exit 0. This loop changes no Python.

**Step 2:**

```bash
codex plugin add ironclaude@ironclaude --json
```

Expected: exit 0, and the JSON `installedPath` is `/Users/roberthyatt/.codex/plugins/cache/ironclaude/ironclaude/1.1.15+codex.20260930172837`.

**Step 3:**

```bash
diff -rq /Users/roberthyatt/Code/ironclaude/worker /Users/roberthyatt/.codex/plugins/cache/ironclaude/ironclaude/1.1.15+codex.20260930172837 -x node_modules -x __pycache__ -x .DS_Store
```

Expected (predicted): no output, exit 0.

**Step 4: Claude plugin cache evidence (read-only, nothing is copied).** Claude Code runs IronClaude's MCP servers from the repo's `worker/` tree, not from the plugin cache. Measured at planning: the state-manager wrapper log `/Users/roberthyatt/.claude/ironclaude-mcp-state-manager.log` records 873 launches from `/Users/roberthyatt/Code/ironclaude/worker/...` and 0 from `/Users/roberthyatt/.claude/plugins/cache/ironclaude/ironclaude/1.1.15/...`. A stale cache copy therefore has no runtime effect, and this step only records that the copy is stale.

```bash
diff -q /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/dist/index.js /Users/roberthyatt/.claude/plugins/cache/ironclaude/ironclaude/1.1.15/mcp-servers/workspace-manager/dist/index.js
```

Expected (predicted): one line, `Files … differ`, exit 1. Record it in the deploy log as a stale copy with no runtime effect, because Claude runs from the repo. Do not copy anything.

**Step 5:** write `docs/plans/2026-10-03-commit-scoping-absolute-deploy-log.md`. It should contain:
- the quoted outputs of Task 1 Steps 6, 9 and 10, and of Task 2 Steps 1–4;
- the orchestrator action: restart Commander after this task. It is not a plan step.
- the note that the Claude plugin cache (`~/.claude/plugins/cache/ironclaude/ironclaude/1.1.15`) holds stale bundles with no runtime effect, because Claude runs IronClaude's MCP servers from the repo (Step 4 evidence).
- the R4 note: the final Fable end review must come back CLEAN before the operator commits through `/commit`. The agent does not commit and nobody pushes. This loop's own `allowed_files` are all repo-relative, so its `/commit` does not exercise absolute-entry handling.

**Step 6: stage the loop docs**

```bash
git -C /Users/roberthyatt/Code/ironclaude add -f docs/plans/2026-10-03-commit-scoping-absolute-requirements.md docs/plans/2026-10-03-commit-scoping-absolute-design.md docs/plans/2026-10-03-commit-scoping-absolute-deploy-log.md docs/plans/2026-10-03-commit-scoping-absolute.md docs/plans/2026-10-03-commit-scoping-absolute.plan.json
```

Expected: no output, exit 0.

**Step 7:**

```bash
git -C /Users/roberthyatt/Code/ironclaude diff --cached --name-only -- docs/plans
```

Expected: these three lines, plus any other `docs/plans` paths the orchestrator staged:
- `docs/plans/2026-10-03-commit-scoping-absolute-deploy-log.md`
- `docs/plans/2026-10-03-commit-scoping-absolute-design.md`
- `docs/plans/2026-10-03-commit-scoping-absolute-requirements.md`
- `docs/plans/2026-10-03-commit-scoping-absolute.md`
- `docs/plans/2026-10-03-commit-scoping-absolute.plan.json`
