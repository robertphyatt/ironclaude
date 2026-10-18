import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import fs from 'fs';
import os from 'os';
import path from 'path';
import { spawnSync } from 'child_process';
import { fileURLToPath } from 'url';

const here = path.dirname(fileURLToPath(import.meta.url));
const pkgRoot = path.resolve(here, '..');
let tmp: string;

function env() {
  return {
    EPISODIC_MEMORY_CONFIG_DIR: path.join(tmp, 'config'),
    TEST_DB_PATH: path.join(tmp, 'db.sqlite'),
    TEST_ARCHIVE_DIR: path.join(tmp, 'archive'),
  };
}
beforeEach(() => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'maintain-cli-'));
  Object.assign(process.env, env());
});
afterEach(() => {
  vi.restoreAllMocks();
  fs.rmSync(tmp, { recursive: true, force: true });
  delete process.env.EPISODIC_MEMORY_CONFIG_DIR;
  delete process.env.TEST_DB_PATH;
  delete process.env.TEST_ARCHIVE_DIR;
});

describe('maintain CLI', () => {
  it('rejects unknown options with exit code 2', async () => {
    const { main } = await import('./maintain-cli.js');
    expect(await main(['--bogus'])).toBe(2);
  });

  it('exits 3 while another live process holds the lock, and leaves that lock alone', async () => {
    const { main } = await import('./maintain-cli.js');
    const { getLockPath } = await import('./sync-lock.js');
    fs.writeFileSync(getLockPath(), JSON.stringify({ pid: process.ppid, startedAt: Date.now(), kind: 'sync' }));
    expect(await main(['--dry-run'])).toBe(3);
    expect(JSON.parse(fs.readFileSync(getLockPath(), 'utf-8')).pid).toBe(process.ppid);
  });

  it('a dry-run runs every phase and releases the lock', async () => {
    const { main } = await import('./maintain-cli.js');
    const { getLockPath } = await import('./sync-lock.js');
    const log = vi.spyOn(console, 'log').mockImplementation(() => {});
    expect(await main(['--dry-run'])).toBe(0);
    const lines = log.mock.calls.map(c => String(c[0]));
    expect(lines.map(l => l.split(':')[0])).toEqual(['slim', 'backfill', 'compact', 'mirror']);
    expect(fs.existsSync(getLockPath())).toBe(false);
  });

  it('--only=compact runs only compaction', async () => {
    const { main } = await import('./maintain-cli.js');
    const log = vi.spyOn(console, 'log').mockImplementation(() => {});
    expect(await main(['--only=compact', '--dry-run'])).toBe(0);
    expect(log.mock.calls.map(c => String(c[0]).split(':')[0])).toEqual(['compact']);
  });

  it('is wired into the bundle, the episodic-memory CLI and the auto-build wrapper', () => {
    const pkg = JSON.parse(fs.readFileSync(path.join(pkgRoot, 'package.json'), 'utf-8'));
    expect(pkg.scripts.bundle).toContain('esbuild src/maintain-cli.ts --bundle');
    expect(pkg.scripts.bundle).toContain('--outfile=dist/maintain-cli.js');
    const cli = fs.readFileSync(path.join(pkgRoot, 'cli', 'episodic-memory.js'), 'utf-8');
    expect(cli).toContain("case 'maintain':");
    expect(cli).toContain("join(distDir, 'maintain-cli.js')");
    const wrapper = fs.readFileSync(path.join(pkgRoot, 'cli', 'mcp-server-wrapper.js'), 'utf-8');
    expect(wrapper).toContain("join(EPISODIC_MEMORY_ROOT, 'dist', 'maintain-cli.js')");
    expect(wrapper).toContain('--outfile=dist/maintain-cli.js');
  });
});

describe('sync-cli under the lock', () => {
  const runSyncCli = () => spawnSync(process.execPath, ['--import', 'tsx', path.join(pkgRoot, 'src', 'sync-cli.ts')], {
    cwd: pkgRoot, encoding: 'utf-8', timeout: 60_000,
    env: { ...process.env, ...env(), HOME: path.join(tmp, 'home') },
  });

  it('skips the whole run while a live process holds the lock', async () => {
    const { getLockPath } = await import('./sync-lock.js');
    fs.writeFileSync(getLockPath(), JSON.stringify({ pid: process.ppid, startedAt: Date.now(), kind: 'cli' }));
    const r = runSyncCli();
    expect(r.status).toBe(0);
    expect(r.stdout).toContain('holds');
    expect(r.stdout).not.toContain('Syncing conversations');
  }, 60_000);

  it('runs sync then maintain on an empty new install, exits 0 and releases the lock', async () => {
    const { getLockPath } = await import('./sync-lock.js');
    fs.mkdirSync(path.join(tmp, 'home'), { recursive: true });
    const r = runSyncCli();
    expect(r.status).toBe(0);
    expect(r.stdout).toContain('Syncing conversations');
    expect(r.stdout).toContain('Running maintain');
    expect(fs.existsSync(getLockPath())).toBe(false);
  }, 60_000);
});
