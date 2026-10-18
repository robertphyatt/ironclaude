import { execFileSync } from 'node:child_process';
import { existsSync, mkdtempSync, mkdirSync, realpathSync, rmSync, statSync, symlinkSync, writeFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { buildScopedStagedTree } from '../scoped-tree.js';

const tempDirs: string[] = [];

function makeTempRepo(): string {
  const dir = mkdtempSync(path.join(os.tmpdir(), 'ironclaude-scoped-tree-'));
  tempDirs.push(dir);
  execFileSync('git', ['-C', dir, 'init', '--initial-branch=main']);
  execFileSync('git', ['-C', dir, 'config', 'user.email', 'test@example.com']);
  execFileSync('git', ['-C', dir, 'config', 'user.name', 'Test User']);
  return dir;
}

function makeTempDir(): string {
  const dir = mkdtempSync(path.join(os.tmpdir(), 'ironclaude-scoped-tree-outside-'));
  tempDirs.push(dir);
  return dir;
}

afterEach(() => {
  while (tempDirs.length > 0) {
    const dir = tempDirs.pop();
    if (dir) rmSync(dir, { recursive: true, force: true });
  }
});

describe('buildScopedStagedTree', () => {
  it('overlays only the allowed staged path, excluding foreign staged entries', () => {
    const repo = makeTempRepo();
    writeFileSync(path.join(repo, 'base.txt'), 'base\n');
    execFileSync('git', ['-C', repo, 'add', 'base.txt']);
    execFileSync('git', ['-C', repo, 'commit', '-m', 'base commit']);
    const parentOid = execFileSync('git', ['-C', repo, 'rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();

    writeFileSync(path.join(repo, 'a.ts'), 'export const a = 1;\n');
    execFileSync('git', ['-C', repo, 'add', 'a.ts']);
    writeFileSync(path.join(repo, 'foreign.ts'), 'export const foreign = 1;\n');
    execFileSync('git', ['-C', repo, 'add', 'foreign.ts']);

    const aOid = execFileSync('git', ['-C', repo, 'rev-parse', ':a.ts'], { encoding: 'utf8' }).trim();

    const tree = buildScopedStagedTree(repo, parentOid, ['a.ts']);
    const listing = execFileSync('git', ['-C', repo, 'ls-tree', '-r', tree], { encoding: 'utf8' });

    expect(listing).toContain('a.ts');
    expect(listing).toContain(aOid);
    expect(listing).not.toContain('foreign.ts');
  });

  it('reflects a staged deletion of an allowed path', () => {
    const repo = makeTempRepo();
    writeFileSync(path.join(repo, 'd.ts'), 'export const d = 1;\n');
    execFileSync('git', ['-C', repo, 'add', 'd.ts']);
    execFileSync('git', ['-C', repo, 'commit', '-m', 'base commit with d.ts']);
    const parentOid = execFileSync('git', ['-C', repo, 'rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();

    execFileSync('git', ['-C', repo, 'rm', '--cached', 'd.ts']);

    const tree = buildScopedStagedTree(repo, parentOid, ['d.ts']);
    const listing = execFileSync('git', ['-C', repo, 'ls-tree', '-r', tree], { encoding: 'utf8' });

    expect(listing).not.toContain('d.ts');
  });

  it('excludes a foreign staged entry that shadows an allowed file name as a directory', () => {
    const repo = makeTempRepo();
    writeFileSync(path.join(repo, 'base.txt'), 'base\n');
    execFileSync('git', ['-C', repo, 'add', 'base.txt']);
    execFileSync('git', ['-C', repo, 'commit', '-m', 'base']);
    const parentOid = execFileSync('git', ['-C', repo, 'rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();
    mkdirSync(path.join(repo, 'work.txt'));
    writeFileSync(path.join(repo, 'work.txt', 'evil'), 'foreign\n');
    execFileSync('git', ['-C', repo, 'add', 'work.txt/evil']);
    const foreignOid = execFileSync('git', ['-C', repo, 'rev-parse', ':work.txt/evil'], { encoding: 'utf8' }).trim();

    const tree = buildScopedStagedTree(repo, parentOid, ['work.txt']);
    const listing = execFileSync('git', ['-C', repo, 'ls-tree', '-r', tree], { encoding: 'utf8' });
    expect(listing).not.toContain('work.txt');
    expect(listing).not.toContain(foreignOid);
    expect(listing).toContain('base.txt');
  });

  it('does not treat a glob-shaped allowed entry as a pathspec (commits no arbitrary file)', () => {
    const repo = makeTempRepo();
    writeFileSync(path.join(repo, 'base.txt'), 'base\n');
    execFileSync('git', ['-C', repo, 'add', 'base.txt']);
    execFileSync('git', ['-C', repo, 'commit', '-m', 'base']);
    const parentOid = execFileSync('git', ['-C', repo, 'rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();
    writeFileSync(path.join(repo, 'a.ts'), 'export const a = 1;\n');
    writeFileSync(path.join(repo, 'b.ts'), 'export const b = 2;\n');
    execFileSync('git', ['-C', repo, 'add', 'a.ts', 'b.ts']);
    const aOid = execFileSync('git', ['-C', repo, 'rev-parse', ':a.ts'], { encoding: 'utf8' }).trim();

    const tree = buildScopedStagedTree(repo, parentOid, ['*.ts']);
    const listing = execFileSync('git', ['-C', repo, 'ls-tree', '-r', tree], { encoding: 'utf8' });
    expect(listing).not.toContain('*.ts');
    expect(listing).not.toContain(aOid);
  });

  it('overlays multiple exact allowed paths and excludes a foreign staged file', () => {
    const repo = makeTempRepo();
    writeFileSync(path.join(repo, 'base.txt'), 'base\n');
    execFileSync('git', ['-C', repo, 'add', 'base.txt']);
    execFileSync('git', ['-C', repo, 'commit', '-m', 'base']);
    const parentOid = execFileSync('git', ['-C', repo, 'rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();
    writeFileSync(path.join(repo, 'a.ts'), 'export const a = 1;\n');
    writeFileSync(path.join(repo, 'b.ts'), 'export const b = 2;\n');
    writeFileSync(path.join(repo, 'c.ts'), 'export const c = 3;\n');
    execFileSync('git', ['-C', repo, 'add', 'a.ts', 'b.ts', 'c.ts']);

    const tree = buildScopedStagedTree(repo, parentOid, ['a.ts', 'b.ts']);
    const names = execFileSync('git', ['-C', repo, 'ls-tree', '-r', '--name-only', tree], { encoding: 'utf8' }).split('\n');
    expect(names).toContain('a.ts');
    expect(names).toContain('b.ts');
    expect(names).not.toContain('c.ts');
  });

  it('throws on an unmerged (conflicted) index entry for an allowed path', () => {
    const repo = makeTempRepo();
    writeFileSync(path.join(repo, 'work.txt'), 'base\n');
    execFileSync('git', ['-C', repo, 'add', 'work.txt']);
    execFileSync('git', ['-C', repo, 'commit', '-m', 'base']);
    execFileSync('git', ['-C', repo, 'checkout', '-b', 'other']);
    writeFileSync(path.join(repo, 'work.txt'), 'other side\n');
    execFileSync('git', ['-C', repo, 'commit', '-am', 'other change']);
    execFileSync('git', ['-C', repo, 'checkout', 'main']);
    writeFileSync(path.join(repo, 'work.txt'), 'my side\n');
    execFileSync('git', ['-C', repo, 'commit', '-am', 'my change']);
    const parentOid = execFileSync('git', ['-C', repo, 'rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();
    try {
      execFileSync('git', ['-C', repo, 'merge', 'other']);
    } catch { /* expected conflict */ }

    expect(() => buildScopedStagedTree(repo, parentOid, ['work.txt'])).toThrow(/unmerged|merge conflict/i);
  });

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

  it('fails when an absolute entry has a looped ancestor, while a proven outside entry is skipped', () => {
    const repo = makeTempRepo();
    writeFileSync(path.join(repo, 'base.txt'), 'base\n');
    execFileSync('git', ['-C', repo, 'add', 'base.txt']);
    execFileSync('git', ['-C', repo, 'commit', '-m', 'base']);
    const parentOid = execFileSync('git', ['-C', repo, 'rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();
    const parentTree = execFileSync('git', ['-C', repo, 'rev-parse', `${parentOid}^{tree}`], { encoding: 'utf8' }).trim();
    const outside = makeTempDir();
    const loop = path.join(repo, 'loop');
    symlinkSync(loop, loop);

    expect(buildScopedStagedTree(repo, parentOid, [path.join(outside, 'outside.txt')])).toBe(parentTree);
    expect(buildScopedStagedTree(repo, parentOid, [path.join(repo, 'base.txt', 'missing', 'entry.txt')])).toBe(parentTree);
    expect(() => buildScopedStagedTree(repo, parentOid, [path.join(loop, 'entry.txt')]))
      .toThrow(/Cannot scope commit.*(ELOOP|loop|resolve)/i);
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
});
