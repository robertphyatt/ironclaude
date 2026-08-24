import { afterEach, describe, expect, it } from 'vitest';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { carryForwardFastForward, changedPaths, dirtyAndUntrackedPaths } from '../git.js';

function git(cwd: string, ...args: string[]): string {
  return execFileSync('git', ['-C', cwd, ...args], { encoding: 'utf8' }).trim();
}

describe('git.ts pure helpers', () => {
  const directories: string[] = [];

  afterEach(() => {
    for (const directory of directories.splice(0)) rmSync(directory, { recursive: true, force: true });
  });

  function repository(): string {
    const root = mkdtempSync(join(tmpdir(), 'ironclaude-git-helpers-'));
    directories.push(root);
    git(root, 'init', '-q', '--initial-branch=main');
    git(root, 'config', 'user.name', 'Git Helpers Test');
    git(root, 'config', 'user.email', 'git-helpers@example.invalid');
    return root;
  }

  function worktree(root: string, ref: string): string {
    const path = mkdtempSync(join(tmpdir(), 'ironclaude-git-helpers-wt-'));
    rmSync(path, { recursive: true, force: true });
    directories.push(path);
    git(root, 'worktree', 'add', '-q', '--detach', path, ref);
    return path;
  }

  describe('changedPaths', () => {
    it('returns exactly the files differing between two commits', () => {
      const root = repository();
      writeFileSync(join(root, 'unchanged.txt'), 'stays\n');
      writeFileSync(join(root, 'fileA.txt'), 'baseA\n');
      writeFileSync(join(root, 'fileToDelete.txt'), 'gone\n');
      git(root, 'add', '-A');
      git(root, 'commit', '-q', '-m', 'base');
      const a = git(root, 'rev-parse', 'HEAD');

      writeFileSync(join(root, 'fileA.txt'), 'updatedA\n');
      writeFileSync(join(root, 'fileB.txt'), 'newB\n');
      execFileSync('rm', [join(root, 'fileToDelete.txt')]);
      git(root, 'add', '-A');
      git(root, 'commit', '-q', '-m', 'change');
      const b = git(root, 'rev-parse', 'HEAD');

      const result = changedPaths(root, a, b);
      expect(result.slice().sort()).toEqual(['fileA.txt', 'fileB.txt', 'fileToDelete.txt'].sort());
    });
  });

  describe('dirtyAndUntrackedPaths', () => {
    it('returns staged, unstaged, and untracked paths, excluding gitignored files', () => {
      const root = repository();
      writeFileSync(join(root, '.gitignore'), 'ignored.txt\n');
      writeFileSync(join(root, 'tracked.txt'), 'original\n');
      git(root, 'add', '-A');
      git(root, 'commit', '-q', '-m', 'base');

      // unstaged: modify a tracked file without adding it
      writeFileSync(join(root, 'tracked.txt'), 'modified unstaged\n');
      // staged: a new file added to the index
      writeFileSync(join(root, 'staged.txt'), 'new staged\n');
      git(root, 'add', 'staged.txt');
      // untracked: a new file never added
      writeFileSync(join(root, 'untracked.txt'), 'new untracked\n');
      // gitignored: a new file matched by .gitignore, never added
      writeFileSync(join(root, 'ignored.txt'), 'should not appear\n');

      const result = dirtyAndUntrackedPaths(root);
      expect(result.slice().sort()).toEqual(['staged.txt', 'tracked.txt', 'untracked.txt'].sort());
    });
  });

  describe('carryForwardFastForward', () => {
    function changeSet() {
      const root = repository();
      writeFileSync(join(root, 'README.md'), 'base readme\n');
      writeFileSync(join(root, 'fileA.txt'), 'baseA\n');
      git(root, 'add', '-A');
      git(root, 'commit', '-q', '-m', 'base');
      const from = git(root, 'rev-parse', 'HEAD');

      writeFileSync(join(root, 'fileA.txt'), 'updatedA\n');
      writeFileSync(join(root, 'fileB.txt'), 'newB\n');
      git(root, 'add', '-A');
      git(root, 'commit', '-q', '-m', 'change');
      const to = git(root, 'rev-parse', 'HEAD');

      return { root, from, to };
    }

    it('on a clean tree, produces a tree byte-identical to a `read-tree --reset -u` control', () => {
      const { root, from, to } = changeSet();
      const toTree = git(root, 'rev-parse', `${to}^{tree}`);

      const under_test = worktree(root, from);
      carryForwardFastForward(under_test, from, to);

      const control = worktree(root, from);
      git(control, 'read-tree', '--reset', '-u', to);

      // read-tree -m -u updates the index/working tree, not HEAD itself — HEAD
      // stays at `from`, so the resulting tree is staged as a diff against it.
      // The tree-hash equalities below are the real correctness check.
      expect(git(under_test, 'write-tree')).toBe(toTree);
      expect(git(under_test, 'write-tree')).toBe(git(control, 'write-tree'));
      expect(readFileSync(join(under_test, 'fileA.txt'), 'utf8')).toBe(readFileSync(join(control, 'fileA.txt'), 'utf8'));
      expect(readFileSync(join(under_test, 'fileB.txt'), 'utf8')).toBe(readFileSync(join(control, 'fileB.txt'), 'utf8'));
    });

    it('carries local modification on a path untouched between from..to forward', () => {
      const { root, from, to } = changeSet();
      const wt = worktree(root, from);
      writeFileSync(join(wt, 'README.md'), 'locally modified readme\n');

      carryForwardFastForward(wt, from, to);

      expect(readFileSync(join(wt, 'README.md'), 'utf8')).toBe('locally modified readme\n');
      expect(readFileSync(join(wt, 'fileA.txt'), 'utf8')).toBe('updatedA\n');
      expect(readFileSync(join(wt, 'fileB.txt'), 'utf8')).toBe('newB\n');
    });

    it('throws when a locally-modified path also changed between from..to', () => {
      const { root, from, to } = changeSet();
      const wt = worktree(root, from);
      writeFileSync(join(wt, 'fileA.txt'), 'locally different A\n');

      expect(() => carryForwardFastForward(wt, from, to)).toThrow();
      // A genuine git refusal leaves the local modification untouched, rather
      // than silently applying the incoming change or corrupting the file.
      expect(readFileSync(join(wt, 'fileA.txt'), 'utf8')).toBe('locally different A\n');
    });
  });
});
