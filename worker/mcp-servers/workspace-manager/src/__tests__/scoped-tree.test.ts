import { execFileSync } from 'node:child_process';
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
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
});
