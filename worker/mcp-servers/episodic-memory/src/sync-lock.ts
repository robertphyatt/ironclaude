/**
 * sync-lock.ts — one Node-side lock for the sync worker and the maintain CLI.
 *
 * The hook's mkdir lock (worker/hooks/episodic-memory-sync.sh) is released as soon as
 * `sync-cli --background` forks its detached child, and a manual CLI run never goes
 * through the hook. This lock is held by the process that actually does the work.
 * A sync lock is stale when its PID is dead or it is older than maxAgeMs (the sync
 * worker also has a runtime budget, see startWatchdog). A manual (cli) lock is
 * stale only when its PID is dead: a long manual maintain run is never stolen.
 */
import fs from 'fs';
import path from 'path';
import { getIndexDir } from './paths.js';

export type LockKind = 'sync' | 'cli';
export interface LockHolder { pid: number; startedAt: number; kind?: LockKind; }

export function getLockPath(): string {
  return path.join(getIndexDir(), 'sync.lock');
}

export function pidAlive(pid: number): boolean {
  try { process.kill(pid, 0); return true; }
  catch (error: any) { return error?.code === 'EPERM'; }
}

export function acquireLock(
  lockPath: string, maxAgeMs: number, kind: LockKind, now = Date.now()
): { acquired: boolean; holder?: LockHolder; reclaimed?: boolean } {
  fs.mkdirSync(path.dirname(lockPath), { recursive: true });
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      const fd = fs.openSync(lockPath, 'wx');
      try { fs.writeSync(fd, JSON.stringify({ pid: process.pid, startedAt: now, kind })); }
      finally { fs.closeSync(fd); }
      return attempt === 0 ? { acquired: true } : { acquired: true, reclaimed: true };
    } catch (error: any) {
      if (error?.code !== 'EEXIST') throw error;
      let holder: LockHolder | undefined;
      try { holder = JSON.parse(fs.readFileSync(lockPath, 'utf-8')); } catch { holder = undefined; }
      const stale = !holder || !pidAlive(holder.pid)
        || (holder.kind !== 'cli' && now - holder.startedAt > maxAgeMs);
      if (!stale) return { acquired: false, holder };
      fs.rmSync(lockPath, { force: true });
    }
  }
  return { acquired: false };
}

export function releaseLock(lockPath: string): void {
  try {
    const holder = JSON.parse(fs.readFileSync(lockPath, 'utf-8')) as LockHolder;
    if (holder.pid === process.pid) fs.rmSync(lockPath, { force: true });
  } catch { /* already gone */ }
}

/** Run `onExpire` after `ms`; unref'd, so it never keeps the process alive by itself. */
export function startWatchdog(ms: number, onExpire: () => void): NodeJS.Timeout {
  const timer = setTimeout(onExpire, ms);
  timer.unref();
  return timer;
}
