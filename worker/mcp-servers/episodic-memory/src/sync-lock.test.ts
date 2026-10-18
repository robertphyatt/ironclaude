import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import fs from 'fs';
import os from 'os';
import path from 'path';
import { spawnSync } from 'child_process';

let tmp: string;
let lp: string;
beforeEach(() => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'sync-lock-'));
  lp = path.join(tmp, 'sync.lock');
  process.env.EPISODIC_MEMORY_CONFIG_DIR = path.join(tmp, 'config');
});
afterEach(() => {
  fs.rmSync(tmp, { recursive: true, force: true });
  delete process.env.EPISODIC_MEMORY_CONFIG_DIR;
});
const holder = (h: object) => fs.writeFileSync(lp, JSON.stringify(h));

describe('sync lock', () => {
  it('acquires when free, refuses while a live holder has it, and releases', async () => {
    const { acquireLock, releaseLock } = await import('./sync-lock.js');
    expect(acquireLock(lp, 60_000, 'sync').acquired).toBe(true);
    expect(JSON.parse(fs.readFileSync(lp, 'utf-8')).pid).toBe(process.pid);
    const second = acquireLock(lp, 60_000, 'sync');
    expect(second.acquired).toBe(false);
    expect(second.holder?.pid).toBe(process.pid);
    releaseLock(lp);
    expect(fs.existsSync(lp)).toBe(false);
  });

  it('reclaims a lock whose holder PID is dead', async () => {
    const { acquireLock } = await import('./sync-lock.js');
    const dead = spawnSync(process.execPath, ['-e', '']).pid;
    holder({ pid: dead, startedAt: Date.now(), kind: 'sync' });
    const r = acquireLock(lp, 60_000, 'sync');
    expect(r).toEqual({ acquired: true, reclaimed: true });
  });

  it('reclaims a sync lock older than maxAge even if its PID is alive', async () => {
    const { acquireLock } = await import('./sync-lock.js');
    holder({ pid: process.ppid, startedAt: Date.now() - 10_000, kind: 'sync' });
    expect(acquireLock(lp, 5_000, 'sync')).toEqual({ acquired: true, reclaimed: true });
  });

  it('never reclaims a live manual (cli) run by age', async () => {
    const { acquireLock } = await import('./sync-lock.js');
    holder({ pid: process.ppid, startedAt: Date.now() - 10_000, kind: 'cli' });
    expect(acquireLock(lp, 5_000, 'sync').acquired).toBe(false);
  });

  it('releaseLock leaves a lock owned by another PID', async () => {
    const { releaseLock } = await import('./sync-lock.js');
    holder({ pid: process.ppid, startedAt: Date.now(), kind: 'sync' });
    releaseLock(lp);
    expect(fs.existsSync(lp)).toBe(true);
  });

  it('getLockPath lives in the index dir under the config dir', async () => {
    const { getLockPath } = await import('./sync-lock.js');
    expect(getLockPath()).toBe(path.join(tmp, 'config', 'conversation-index', 'sync.lock'));
  });

  it('startWatchdog fires after its budget and does not keep the process alive', async () => {
    const { startWatchdog } = await import('./sync-lock.js');
    await new Promise<void>(resolve => { startWatchdog(20, resolve); });
    const t = startWatchdog(60_000, () => {});
    expect(t.hasRef()).toBe(false);
    clearTimeout(t);
  });
});
