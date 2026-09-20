import { afterEach, describe, expect, it } from 'vitest';
import { execFileSync } from 'node:child_process';
import { existsSync, lstatSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import {
  addSharedResourceEntries,
  carryForwardFastForward,
  changedPaths,
  directoryContainsSecret,
  discoverRepository,
  dirtyAndUntrackedPaths,
  gitBufferOverflowError,
  linkSharedResources,
  listManagedBranches,
  listWorktrees,
  readSharedResourceConfig,
  runGit,
  runGitEnv,
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
    function identity(): { id: string; primary: string } {
      const root = mkdtempSync(join(tmpdir(), 'ironclaude-git-shared-'));
      mkdirSync(join(root, 'info'), { recursive: true });
      const primary = mkdtempSync(join(tmpdir(), 'ironclaude-git-primary-'));
      return { id: root, primary };
    }

    it('appends valid entries and reports them as added', () => {
      const { id, primary } = identity();
      const result = addSharedResourceEntries(id, ['data/models', 'caches/vision'], primary);
      expect(result.added).toEqual(['data/models', 'caches/vision']);
      expect(result.rejected).toEqual([]);
      expect(readSharedResourceConfig(id)).toEqual(['data/models', 'caches/vision']);
    });

    it('creates the config file when absent', () => {
      const { id, primary } = identity();
      expect(existsSync(join(id, 'info', 'worktree-shared-resources'))).toBe(false);
      addSharedResourceEntries(id, ['data/models'], primary);
      expect(existsSync(join(id, 'info', 'worktree-shared-resources'))).toBe(true);
    });

    it('dedupes an already-present entry', () => {
      const { id, primary } = identity();
      addSharedResourceEntries(id, ['data/models'], primary);
      const result = addSharedResourceEntries(id, ['data/models', 'caches/vision'], primary);
      expect(result.added).toEqual(['caches/vision']);
      expect(result.skipped).toEqual(['data/models']);
      expect(readSharedResourceConfig(id)).toEqual(['data/models', 'caches/vision']);
    });

    it('rejects unsafe entries and never writes them', () => {
      const { id, primary } = identity();
      const bad = ['../escape', '/abs', 'a*', 'trailing/', '!neg', '#comment', 'ok\nescape', 'a\rb', ' models', 'models ', '.', './data', 'models/.', 'a//b'];
      const result = addSharedResourceEntries(id, bad, primary);
      expect(result.rejected).toEqual(bad);
      expect(result.added).toEqual([]);
      expect(readSharedResourceConfig(id)).toEqual([]);
    });

    it('blocks well-known secret entries by default, writing nothing', () => {
      const { id, primary } = identity();
      const result = addSharedResourceEntries(id, ['.env', '.aws/credentials', '.ssh/id_rsa', 'key.pem'], primary);
      expect(result.secretBlocked).toEqual(['.env', '.aws/credentials', '.ssh/id_rsa', 'key.pem']);
      expect(result.added).toEqual([]);
      expect(readSharedResourceConfig(id)).toEqual([]);
    });

    it('still accepts legitimate entries alongside the secret deny-list', () => {
      const { id, primary } = identity();
      const result = addSharedResourceEntries(id, ['data/models', '.venv', 'caches/vision'], primary);
      expect(result.added).toEqual(['data/models', '.venv', 'caches/vision']);
      expect(result.secretBlocked).toEqual([]);
    });

    it('accepts secret entries when the operator explicitly overrides via allowSecretEntries', () => {
      const { id, primary } = identity();
      const result = addSharedResourceEntries(id, ['.env', '.aws/credentials'], primary, true);
      expect(result.added).toEqual(['.env', '.aws/credentials']);
      expect(result.secretBlocked).toEqual([]);
    });

    it('still hard-rejects unsafe entries even with allowSecretEntries set', () => {
      const { id, primary } = identity();
      const result = addSharedResourceEntries(id, ['../escape'], primary, true);
      expect(result.rejected).toEqual(['../escape']);
      expect(result.added).toEqual([]);
      expect(result.secretBlocked).toEqual([]);
    });

    it('scans an explicitly-shared directory and blocks it when it contains a secret file', () => {
      const { id, primary } = identity();
      mkdirSync(join(primary, 'config'), { recursive: true });
      writeFileSync(join(primary, 'config', '.env'), 'X=1\n');
      const result = addSharedResourceEntries(id, ['config'], primary);
      expect(result.secretBlocked).toEqual(['config']);
      expect(result.secretHits.config).toEqual(['config/.env']);
      expect(result.added).toEqual([]);
    });

    it('blocks a directory with a nested secret file several levels down', () => {
      const { id, primary } = identity();
      mkdirSync(join(primary, 'deploy', 'keys'), { recursive: true });
      writeFileSync(join(primary, 'deploy', 'keys', 'id_rsa'), 'fake-key\n');
      const result = addSharedResourceEntries(id, ['deploy'], primary);
      expect(result.secretBlocked).toEqual(['deploy']);
    });

    it('blocks a directory containing a child directory named after a secret-dir convention', () => {
      const { id, primary } = identity();
      mkdirSync(join(primary, 'home', '.ssh'), { recursive: true });
      const result = addSharedResourceEntries(id, ['home'], primary);
      expect(result.secretBlocked).toEqual(['home']);
    });

    it('adds a clean directory with no secret-shaped contents', () => {
      const { id, primary } = identity();
      mkdirSync(join(primary, 'models'), { recursive: true });
      writeFileSync(join(primary, 'models', 'weights.bin'), 'binary\n');
      writeFileSync(join(primary, 'models', 'tokenizer.json'), '{}\n');
      const result = addSharedResourceEntries(id, ['models'], primary);
      expect(result.added).toEqual(['models']);
      expect(result.secretHits).toEqual({});
    });

    it('allows a directory with a secret file when allowSecretEntries overrides', () => {
      const { id, primary } = identity();
      mkdirSync(join(primary, 'config'), { recursive: true });
      writeFileSync(join(primary, 'config', '.env'), 'X=1\n');
      const result = addSharedResourceEntries(id, ['config'], primary, true);
      expect(result.added).toEqual(['config']);
    });

    it('carves out vendor directories from the scan (.venv and node_modules)', () => {
      const { id, primary } = identity();
      mkdirSync(join(primary, '.venv', 'lib', 'python3.12', 'site-packages', 'certifi'), { recursive: true });
      writeFileSync(join(primary, '.venv', 'lib', 'python3.12', 'site-packages', 'certifi', 'cacert.pem'), 'cert\n');
      mkdirSync(join(primary, 'node_modules', 'pkg'), { recursive: true });
      writeFileSync(join(primary, 'node_modules', 'pkg', 'test.pem'), 'cert\n');
      const result = addSharedResourceEntries(id, ['.venv', 'node_modules'], primary);
      expect(result.added).toEqual(['.venv', 'node_modules']);
      expect(result.secretBlocked).toEqual([]);
    });

    it('does not flag a .example file as a secret, at directory-content or name level', () => {
      const { id, primary } = identity();
      mkdirSync(join(primary, 'config2'), { recursive: true });
      writeFileSync(join(primary, 'config2', '.env.example'), 'X=\n');
      const dirResult = addSharedResourceEntries(id, ['config2'], primary);
      expect(dirResult.added).toEqual(['config2']);

      const nameResult = addSharedResourceEntries(id, ['.env.example'], primary);
      expect(nameResult.added).toEqual(['.env.example']);
    });

    it('does not scan an entry whose source is absent from the primary checkout', () => {
      const { id, primary } = identity();
      const result = addSharedResourceEntries(id, ['nope'], primary);
      expect(result.added).toEqual(['nope']);
      expect(result.secretHits).toEqual({});
    });

    it('directoryContainsSecret matches a symlink by name but does not follow it to descend', () => {
      const { primary } = identity();
      const linkDir = join(primary, 'link-dir');
      mkdirSync(linkDir, { recursive: true });
      const realFile = join(primary, 'real-file.txt');
      writeFileSync(realFile, 'x\n');
      symlinkSync(realFile, join(linkDir, 'id_rsa'));
      const realDataDir = join(primary, 'real-data');
      mkdirSync(realDataDir, { recursive: true });
      writeFileSync(join(realDataDir, '.env'), 'X=1\n');
      symlinkSync(realDataDir, join(linkDir, 'data'));

      const result = directoryContainsSecret(linkDir, 'link-dir');
      expect(result.hits).toContain('link-dir/id_rsa');
      expect(result.hits).not.toContain('link-dir/data/.env');
    });

    it('directoryContainsSecret reports truncated when maxEntries is exceeded', () => {
      const { primary } = identity();
      const capDir = join(primary, 'capdir');
      mkdirSync(join(capDir, 'sub'), { recursive: true });
      writeFileSync(join(capDir, 'a.txt'), '1\n');
      writeFileSync(join(capDir, 'b.txt'), '2\n');
      writeFileSync(join(capDir, 'c.txt'), '3\n');
      writeFileSync(join(capDir, 'd.txt'), '4\n');
      writeFileSync(join(capDir, 'e.txt'), '5\n');
      writeFileSync(join(capDir, 'sub', '.env'), 'X=1\n');

      const result = directoryContainsSecret(capDir, 'capdir', { maxEntries: 3 });
      expect(result.hits).toEqual([]);
      expect(result.truncated).toBe(true);
    });

    it('directoryContainsSecret examines all shallow entries before descending, finding a depth-1 secret despite a budget-exhausting sibling subtree', () => {
      const { primary } = identity();
      const bfsDir = join(primary, 'bfsdir');
      const bigsub = join(bfsDir, 'bigsub');
      mkdirSync(bigsub, { recursive: true });
      for (let i = 0; i < 5; i++) {
        writeFileSync(join(bigsub, `file${i}.txt`), String(i));
      }
      writeFileSync(join(bfsDir, 'id_rsa'), 'fake-key\n');
      // Two depth-1 entries (id_rsa, bigsub/) fit maxEntries=2, so a breadth-first
      // walk examines BOTH before descending into bigsub and finds id_rsa regardless
      // of filesystem order. A depth-first "recurse on encounter" walk that dives into
      // bigsub first would exhaust the budget inside it before reaching the sibling
      // secret. Encodes the real shallow-first security guarantee, not a sort artifact.
      const result = directoryContainsSecret(bfsDir, 'bfsdir', { maxEntries: 2 });
      expect(result.hits).toContain('bfsdir/id_rsa');
      expect(result.truncated).toBe(true);
    });

    it('rejects a deeply-nested secret past the default max depth, marking scanTruncated', () => {
      const { id, primary } = identity();
      const deepPath = join(primary, 'deep', 'd1', 'd2', 'd3', 'd4', 'd5', 'd6', 'd7');
      mkdirSync(deepPath, { recursive: true });
      writeFileSync(join(deepPath, '.env'), 'X=1\n');

      const result = addSharedResourceEntries(id, ['deep'], primary);
      expect(result.added).toEqual(['deep']);
      expect(result.scanTruncated).toEqual(['deep']);
      expect(result.secretHits.deep).toBeUndefined();
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

    it('plants a nested entry whose parent dir is absent from the worktree', () => {
      const { primary, worktree, identity } = primaryWithWorktree();
      mkdirSync(join(primary, 'data', 'models'), { recursive: true });
      const planted = linkSharedResources(primary, worktree, identity, ['data/models']);
      expect(planted).toEqual(['data/models']);
      expect(lstatSync(join(worktree, 'data', 'models')).isSymbolicLink()).toBe(true);
    });
  });

  describe('listManagedBranches', () => {
    it('returns sorted ironclaude/* branch names, excluding main', () => {
      const root = repository();
      writeFileSync(join(root, 'README.md'), 'base\n');
      git(root, 'add', '-A');
      git(root, 'commit', '-q', '-m', 'base');
      git(root, 'branch', 'ironclaude/b');
      git(root, 'branch', 'ironclaude/a');

      expect(listManagedBranches(root)).toEqual(['ironclaude/a', 'ironclaude/b']);
    });

    it('returns an empty array when there are no ironclaude/* branches', () => {
      const root = repository();
      writeFileSync(join(root, 'README.md'), 'base\n');
      git(root, 'add', '-A');
      git(root, 'commit', '-q', '-m', 'base');

      expect(listManagedBranches(root)).toEqual([]);
    });

    it('returns the exact branch name when a same-named tag collides (no %(refname:short) mangling)', () => {
      const root = repository();
      writeFileSync(join(root, 'README.md'), 'base\n');
      git(root, 'add', '-A');
      git(root, 'commit', '-q', '-m', 'base');
      git(root, 'branch', 'ironclaude/a');
      git(root, 'tag', 'ironclaude/a');   // refs/tags/ironclaude/a collides with refs/heads/ironclaude/a
      expect(listManagedBranches(root)).toEqual(['ironclaude/a']);
    });
  });

  describe('listWorktrees tolerates a registered worktree whose directory was deleted outside git', () => {
    function managedWorktree(root: string, branch: string): string {
      const path = mkdtempSync(join(tmpdir(), 'ironclaude-git-helpers-wt-'));
      rmSync(path, { recursive: true, force: true });
      directories.push(path);
      git(root, 'worktree', 'add', '-q', '-b', branch, path, 'HEAD');
      return path;
    }

    it('does not throw, still lists the dir-gone worktree, and still lists a second on-disk worktree', () => {
      const root = repository();
      writeFileSync(join(root, 'README.md'), 'base\n');
      git(root, 'add', '-A');
      git(root, 'commit', '-q', '-m', 'base');

      const gone = managedWorktree(root, 'ironclaude/gone');
      const present = managedWorktree(root, 'ironclaude/present');
      // Capture the realpath while the directory still exists: `git worktree
      // add` records the worktree path already realpath'd (e.g. macOS
      // /var -> /private/var), so this is what listWorktrees returns for both
      // entries — canonicalPath's ENOENT fallback (path.resolve, no symlink
      // resolution) reproduces it unchanged because git's stored path is
      // already canonical.
      const goneCanonical = realpathSync(gone);
      const presentCanonical = realpathSync(present);
      rmSync(gone, { recursive: true, force: true });

      let entries: ReturnType<typeof listWorktrees> = [];
      expect(() => {
        entries = listWorktrees(root);
      }).not.toThrow();

      const paths = entries.map((entry) => entry.path);
      expect(paths).toContain(presentCanonical);
      expect(paths).toContain(goneCanonical);

      expect(() => discoverRepository(root)).not.toThrow();
    });
  });

  describe('large-output buffer handling', () => {
    function bigRepository(): { root: string; size: number } {
      const root = repository();
      const content = 'x'.repeat(1_500_000) + '\n';
      writeFileSync(join(root, 'big.txt'), content);
      git(root, 'add', 'big.txt');
      git(root, 'commit', '-q', '-m', 'big file');
      return { root, size: content.length };
    }

    it('runGit returns a >1MB git stdout in full, without throwing', () => {
      const { root } = bigRepository();
      expect(runGit(root, ['show', 'HEAD:big.txt']).length).toBeGreaterThanOrEqual(1_500_000);
    });

    it('runGitEnv returns a >1MB git stdout in full, without throwing', () => {
      const { root } = bigRepository();
      expect(runGitEnv(root, ['show', 'HEAD:big.txt'], { ...process.env }).length).toBeGreaterThanOrEqual(1_500_000);
    });

    describe('gitBufferOverflowError', () => {
      it('returns an Error naming the joined argv for an ENOBUFS error', () => {
        const args = ['diff', '--binary', '--full-index', 'base', 'head'];
        const error = Object.assign(new Error('x'), { code: 'ENOBUFS' }) as NodeJS.ErrnoException;
        const result = gitBufferOverflowError(args, error);
        expect(result).toBeInstanceOf(Error);
        expect(result?.message).toContain(args.join(' '));
      });

      it('returns undefined for a non-ENOBUFS error', () => {
        const args = ['status'];
        const error = Object.assign(new Error('x'), { code: 'EACCES' }) as NodeJS.ErrnoException;
        expect(gitBufferOverflowError(args, error)).toBeUndefined();
      });

      it('returns undefined for an undefined error', () => {
        expect(gitBufferOverflowError(['status'], undefined)).toBeUndefined();
      });
    });

  });
});
