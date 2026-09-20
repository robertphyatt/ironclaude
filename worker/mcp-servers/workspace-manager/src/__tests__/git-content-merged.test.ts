import { afterEach, describe, expect, it, vi } from 'vitest';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { contentMergedInto, isAncestor, runGit } from '../git.js';

const spawnCalls = vi.hoisted(() => ({ list: [] as string[][] }));
vi.mock('node:child_process', async (importOriginal) => {
  const actual = await importOriginal<typeof import('node:child_process')>();
  return {
    ...actual,
    spawnSync: (cmd: string, args?: readonly string[], opts?: any) => {
      spawnCalls.list.push([cmd, ...(args ?? [])]);
      return (actual.spawnSync as any)(cmd, args, opts);
    },
  };
});

function git(cwd: string, ...args: string[]): string {
  return execFileSync('git', ['-C', cwd, ...args], { encoding: 'utf8' }).trim();
}

function gitVersionAtLeast(major: number, minor: number): boolean {
  const raw = execFileSync('git', ['--version'], { encoding: 'utf8' }).trim();
  const match = raw.match(/(\d+)\.(\d+)\.(\d+)/);
  if (!match) return false;
  const [, maj, min] = match;
  const majorNum = Number(maj);
  const minorNum = Number(min);
  return majorNum > major || (majorNum === major && minorNum >= minor);
}

describe('contentMergedInto', () => {
  const directories: string[] = [];

  afterEach(() => {
    for (const directory of directories.splice(0)) rmSync(directory, { recursive: true, force: true });
  });

  function repository(): string {
    const root = mkdtempSync(join(tmpdir(), 'ironclaude-content-merged-'));
    directories.push(root);
    git(root, 'init', '-q', '--initial-branch=main');
    git(root, 'config', 'user.name', 'Content Merged Test');
    git(root, 'config', 'user.email', 'content-merged@example.invalid');
    return root;
  }

  function scratch(): string {
    const dir = mkdtempSync(join(tmpdir(), 'ironclaude-content-merged-scratch-'));
    directories.push(dir);
    return dir;
  }

  it('detects squash-merged content as merged even though isAncestor is false, and rejects a genuinely unmerged branch', () => {
    const root = repository();

    // Base commit on main.
    writeFileSync(join(root, 'base.txt'), 'base\n');
    git(root, 'add', '-A');
    git(root, 'commit', '-q', '-m', 'base commit');

    // Feature branch with its own commits.
    git(root, 'checkout', '-q', '-b', 'feature');
    writeFileSync(join(root, 'feature.txt'), 'feature line 1\n');
    git(root, 'add', '-A');
    git(root, 'commit', '-q', '-m', 'feature commit 1');
    writeFileSync(join(root, 'feature.txt'), 'feature line 1\nfeature line 2\n');
    git(root, 'add', '-A');
    git(root, 'commit', '-q', '-m', 'feature commit 2');
    const featureTip = git(root, 'rev-parse', 'HEAD');

    // A genuinely unmerged branch, diverged from main and never merged anywhere.
    git(root, 'checkout', '-q', 'main');
    git(root, 'checkout', '-q', '-b', 'unmerged');
    writeFileSync(join(root, 'unmerged.txt'), 'never merged\n');
    git(root, 'add', '-A');
    git(root, 'commit', '-q', '-m', 'unmerged commit');
    const unmergedTip = git(root, 'rev-parse', 'HEAD');

    // Squash-merge feature into main under a NEW sha (main's history never
    // contains featureTip as an ancestor).
    git(root, 'checkout', '-q', 'main');
    git(root, 'merge', '-q', '--squash', 'feature');
    git(root, 'commit', '-q', '-m', 'squash-merge feature into main');

    expect(isAncestor(root, featureTip, 'main')).toBe(false);

    const scratchDir = scratch();

    // (a) squashed content is detected as merged.
    expect(contentMergedInto(root, featureTip, 'main', scratchDir)).toBe(true);

    // (b) a genuinely unmerged tip is not detected as merged.
    expect(contentMergedInto(root, unmergedTip, 'main', scratchDir)).toBe(false);

    // (c) no object-DB writes and no working-tree/index changes by contentMergedInto.
    const beforeCounts = runGit(root, ['count-objects', '-v']);
    const beforeRefs = runGit(root, ['for-each-ref']);
    expect(contentMergedInto(root, featureTip, 'main', scratchDir)).toBe(true);
    const afterCounts = runGit(root, ['count-objects', '-v']);
    const afterRefs = runGit(root, ['for-each-ref']);
    expect(afterCounts).toEqual(beforeCounts);
    expect(afterRefs).toEqual(beforeRefs);
    expect(runGit(root, ['status', '--porcelain'])).toEqual('');

    // (d) negative control: prove the object-write assertion above is capable
    // of failing, by actually writing objects with `merge-tree --write-tree`.
    if (gitVersionAtLeast(2, 38)) {
      const before = runGit(root, ['count-objects', '-v']);
      const beforeCountLine = before.match(/^count: (\d+)/m)?.[1];
      execFileSync('git', ['-C', root, 'merge-tree', '--write-tree', 'main', 'unmerged'], { encoding: 'utf8' });
      const after = runGit(root, ['count-objects', '-v']);
      const afterCountLine = after.match(/^count: (\d+)/m)?.[1];
      expect(afterCountLine).not.toEqual(beforeCountLine);
    }
    // else: git --version too old for `merge-tree --write-tree`; negative control skipped.
  });

  /**
   * Builds a genuinely-unmerged branch (two commits, fileA.txt + fileB.txt,
   * never merged into main) and advances main by `mainCommits` non-merge
   * commits, half of them editing fileA.txt. Returns the branch tip.
   */
  function buildUnmergedScenario(root: string, mainCommits: number): string {
    writeFileSync(join(root, 'base.txt'), 'base\n');
    git(root, 'add', '-A');
    git(root, 'commit', '-q', '-m', 'base commit');

    git(root, 'checkout', '-q', '-b', 'unmerged-branch');
    writeFileSync(join(root, 'fileA.txt'), 'branch A\n');
    git(root, 'add', '-A');
    git(root, 'commit', '-q', '-m', 'branch commit A');
    writeFileSync(join(root, 'fileB.txt'), 'branch B\n');
    git(root, 'add', '-A');
    git(root, 'commit', '-q', '-m', 'branch commit B');
    const branchTip = git(root, 'rev-parse', 'HEAD');

    git(root, 'checkout', '-q', 'main');
    for (let i = 0; i < mainCommits; i++) {
      if (i % 2 === 0) {
        writeFileSync(join(root, 'fileA.txt'), `main edit ${i}\n`);
      } else {
        writeFileSync(join(root, `other-${i}.txt`), `main other ${i}\n`);
      }
      git(root, 'add', '-A');
      git(root, 'commit', '-q', '-m', `main commit ${i}`);
    }

    return branchTip;
  }

  it('BOUND PROOF: spawnSync call count for a genuinely-unmerged tip does not grow with the number of intervening main commits', () => {
    const rootK5 = repository();
    const branchTipK5 = buildUnmergedScenario(rootK5, 5);
    const scratchK5 = scratch();
    spawnCalls.list.length = 0;
    contentMergedInto(rootK5, branchTipK5, 'main', scratchK5);
    const callsK5 = spawnCalls.list.map((call) => [...call]);
    const countK5 = callsK5.length;

    const rootK60 = repository();
    const branchTipK60 = buildUnmergedScenario(rootK60, 60);
    const scratchK60 = scratch();
    spawnCalls.list.length = 0;
    contentMergedInto(rootK60, branchTipK60, 'main', scratchK60);
    const countK60 = spawnCalls.list.length;

    expect(countK5).toEqual(countK60);
    expect(countK5).toBeLessThanOrEqual(12);
    expect(callsK5.some((call) => call.join(' ').includes('patch-id'))).toBe(true);
    expect(callsK5.some((call) => call.join(' ').includes('cherry'))).toBe(true);
  });

  it('DETECTION PRESERVED: a squash-merged multi-commit branch with a later overlapping main edit is still detected as merged', () => {
    const root = repository();
    writeFileSync(join(root, 'base.txt'), 'base\n');
    git(root, 'add', '-A');
    git(root, 'commit', '-q', '-m', 'base commit');

    git(root, 'checkout', '-q', '-b', 'squash-feature');
    writeFileSync(join(root, 'feature.txt'), 'line 1\n');
    git(root, 'add', '-A');
    git(root, 'commit', '-q', '-m', 'feature commit 1');
    writeFileSync(join(root, 'feature.txt'), 'line 1\nline 2\n');
    git(root, 'add', '-A');
    git(root, 'commit', '-q', '-m', 'feature commit 2');
    const branchTip = git(root, 'rev-parse', 'HEAD');

    git(root, 'checkout', '-q', 'main');
    git(root, 'merge', '-q', '--squash', 'squash-feature');
    git(root, 'commit', '-q', '-m', 'squash-merge feature into main');

    // Later main commit overlapping-edits the same lines.
    writeFileSync(join(root, 'feature.txt'), 'line 1\nline 2 overlapping edit\n');
    git(root, 'add', '-A');
    git(root, 'commit', '-q', '-m', 'later overlapping main edit');

    const cherryOutput = git(root, 'cherry', 'main', branchTip);
    const cherryLines = cherryOutput.split('\n').filter((line) => line.length > 0);
    expect(cherryLines.length).toBeGreaterThan(0);
    for (const line of cherryLines) {
      expect(line.startsWith('+ ')).toBe(true);
    }

    const scratchDir = scratch();
    expect(contentMergedInto(root, branchTip, 'main', scratchDir)).toBe(true);
  });

  it('PARTIAL LANDING: only one of two branch-touched files landing identically on main is not detected as merged', () => {
    const root = repository();
    writeFileSync(join(root, 'base.txt'), 'base\n');
    git(root, 'add', '-A');
    git(root, 'commit', '-q', '-m', 'base commit');

    git(root, 'checkout', '-q', '-b', 'partial-feature');
    writeFileSync(join(root, 'fileX.txt'), 'shared content\n');
    writeFileSync(join(root, 'fileY.txt'), 'unlanded content\n');
    git(root, 'add', '-A');
    git(root, 'commit', '-q', '-m', 'branch commit touching fileX and fileY');
    const branchTip = git(root, 'rev-parse', 'HEAD');

    git(root, 'checkout', '-q', 'main');
    writeFileSync(join(root, 'fileX.txt'), 'shared content\n');
    git(root, 'add', '-A');
    git(root, 'commit', '-q', '-m', 'main lands only fileX identically');

    const scratchDir = scratch();
    expect(contentMergedInto(root, branchTip, 'main', scratchDir)).toBe(false);
  });

  it('LITERAL PATHSPEC: a squash-merged branch touching a file literally named weird[1].txt is still detected as merged', () => {
    const root = repository();
    writeFileSync(join(root, 'base.txt'), 'base\n');
    git(root, 'add', '-A');
    git(root, 'commit', '-q', '-m', 'base commit');

    git(root, 'checkout', '-q', '-b', 'weird-feature');
    writeFileSync(join(root, 'weird[1].txt'), 'line 1\n');
    git(root, 'add', '-A');
    git(root, 'commit', '-q', '-m', 'weird commit 1');
    writeFileSync(join(root, 'weird[1].txt'), 'line 1\nline 2\n');
    git(root, 'add', '-A');
    git(root, 'commit', '-q', '-m', 'weird commit 2');
    const branchTip = git(root, 'rev-parse', 'HEAD');

    git(root, 'checkout', '-q', 'main');
    git(root, 'merge', '-q', '--squash', 'weird-feature');
    git(root, 'commit', '-q', '-m', 'squash-merge weird feature into main');

    writeFileSync(join(root, 'weird[1].txt'), 'line 1\nline 2 overlapping edit\n');
    git(root, 'add', '-A');
    git(root, 'commit', '-q', '-m', 'later overlapping main edit on weird file');

    const cherryOutput = git(root, 'cherry', 'main', branchTip);
    const cherryLines = cherryOutput.split('\n').filter((line) => line.length > 0);
    expect(cherryLines.length).toBeGreaterThan(0);
    for (const line of cherryLines) {
      expect(line.startsWith('+ ')).toBe(true);
    }

    const scratchDir = scratch();
    expect(contentMergedInto(root, branchTip, 'main', scratchDir)).toBe(true);
  });

  it('EMPTY-DIFF TIP: a branch whose net diff against merge-base is empty is not detected as merged', () => {
    const root = repository();
    writeFileSync(join(root, 'base.txt'), 'base\n');
    git(root, 'add', '-A');
    git(root, 'commit', '-q', '-m', 'base commit');

    git(root, 'checkout', '-q', '-b', 'empty-diff-feature');
    writeFileSync(join(root, 'transient.txt'), 'temporary\n');
    git(root, 'add', '-A');
    git(root, 'commit', '-q', '-m', 'add transient file');
    rmSync(join(root, 'transient.txt'));
    git(root, 'add', '-A');
    git(root, 'commit', '-q', '-m', 'revert transient file');
    const branchTip = git(root, 'rev-parse', 'HEAD');

    const scratchDir = scratch();
    expect(contentMergedInto(root, branchTip, 'main', scratchDir)).toBe(false);
  });
});
