import { afterEach, describe, expect, it } from 'vitest';
import { execFileSync } from 'node:child_process';
import { existsSync, lstatSync, mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import {
  addSharedResourceEntries,
  carryForwardFastForward,
  changedPaths,
  dirtyAndUntrackedPaths,
  linkSharedResources,
  readSharedResourceConfig,
} from '../git.js';

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

  describe('shared-resource config', () => {
    // A repositoryIdentity is a git-common-dir; the config lives under its info/ subdir.
    function identity(): string {
      const root = mkdtempSync(join(tmpdir(), 'ironclaude-git-shared-'));
      mkdirSync(join(root, 'info'), { recursive: true });
      return root;
    }

    it('appends valid entries and reports them as added', () => {
      const id = identity();
      const result = addSharedResourceEntries(id, ['data/models', 'caches/vision']);
      expect(result.added).toEqual(['data/models', 'caches/vision']);
      expect(result.rejected).toEqual([]);
      expect(readSharedResourceConfig(id)).toEqual(['data/models', 'caches/vision']);
    });

    it('creates the config file when absent', () => {
      const id = identity();
      expect(existsSync(join(id, 'info', 'worktree-shared-resources'))).toBe(false);
      addSharedResourceEntries(id, ['data/models']);
      expect(existsSync(join(id, 'info', 'worktree-shared-resources'))).toBe(true);
    });

    it('dedupes an already-present entry', () => {
      const id = identity();
      addSharedResourceEntries(id, ['data/models']);
      const result = addSharedResourceEntries(id, ['data/models', 'caches/vision']);
      expect(result.added).toEqual(['caches/vision']);
      expect(result.skipped).toEqual(['data/models']);
      expect(readSharedResourceConfig(id)).toEqual(['data/models', 'caches/vision']);
    });

    it('rejects unsafe entries and never writes them', () => {
      const id = identity();
      const bad = ['../escape', '/abs', 'a*', 'trailing/', '!neg', '#comment', 'ok\nescape', 'a\rb', ' models', 'models ', '.', './data', 'models/.', 'a//b'];
      const result = addSharedResourceEntries(id, bad);
      expect(result.rejected).toEqual(bad);
      expect(result.added).toEqual([]);
      expect(readSharedResourceConfig(id)).toEqual([]);
    });

    it('blocks well-known secret entries by default, writing nothing', () => {
      const id = identity();
      const result = addSharedResourceEntries(id, ['.env', '.aws/credentials', '.ssh/id_rsa', 'key.pem']);
      expect(result.secretBlocked).toEqual(['.env', '.aws/credentials', '.ssh/id_rsa', 'key.pem']);
      expect(result.added).toEqual([]);
      expect(readSharedResourceConfig(id)).toEqual([]);
    });

    it('still accepts legitimate entries alongside the secret deny-list', () => {
      const id = identity();
      const result = addSharedResourceEntries(id, ['data/models', '.venv', 'caches/vision']);
      expect(result.added).toEqual(['data/models', '.venv', 'caches/vision']);
      expect(result.secretBlocked).toEqual([]);
    });

    it('accepts secret entries when the operator explicitly overrides via allowSecretEntries', () => {
      const id = identity();
      const result = addSharedResourceEntries(id, ['.env', '.aws/credentials'], true);
      expect(result.added).toEqual(['.env', '.aws/credentials']);
      expect(result.secretBlocked).toEqual([]);
    });

    it('still hard-rejects unsafe entries even with allowSecretEntries set', () => {
      const id = identity();
      const result = addSharedResourceEntries(id, ['../escape'], true);
      expect(result.rejected).toEqual(['../escape']);
      expect(result.added).toEqual([]);
      expect(result.secretBlocked).toEqual([]);
    });
  });

  describe('linkSharedResources returns the planted set', () => {
    function primaryWithWorktree(): { primary: string; worktree: string; identity: string } {
      const root = mkdtempSync(join(tmpdir(), 'ironclaude-git-link-'));
      const primary = join(root, 'primary');
      const worktree = join(root, 'worktree');
      const identityDir = join(root, 'common');
      mkdirSync(primary, { recursive: true });
      mkdirSync(worktree, { recursive: true });
      mkdirSync(join(identityDir, 'info'), { recursive: true });
      return { primary, worktree, identity: identityDir };
    }

    it('returns entries actually planted and omits a source-absent entry', () => {
      const { primary, worktree, identity } = primaryWithWorktree();
      // present source
      mkdirSync(join(primary, 'data'), { recursive: true });
      const planted = linkSharedResources(primary, worktree, identity, ['data', 'absent_dir']);
      expect(planted).toEqual(['data']);
      expect(lstatSync(join(worktree, 'data')).isSymbolicLink()).toBe(true);
      expect(existsSync(join(worktree, 'absent_dir'))).toBe(false);
    });
  });
});
