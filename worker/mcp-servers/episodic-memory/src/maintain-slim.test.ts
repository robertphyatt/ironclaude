import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import fs from 'fs';
import os from 'os';
import path from 'path';
import Database from 'better-sqlite3';
import * as sqliteVec from 'sqlite-vec';
import { makeLegacyDb, legacyVector } from './__fixtures__/make-legacy-db.js';

const BIG = () => 1e15;
const TINY = () => 1;
let tmp: string;
let dbPath: string;

function query(file: string, sql: string, ...params: unknown[]): any[] {
  const db = new Database(file, { readonly: true });
  sqliteVec.load(db);
  try { return db.prepare(sql).all(...params); } finally { db.close(); }
}
const tables = (file: string) => query(file, `SELECT name FROM sqlite_master WHERE type = 'table'`).map(r => r.name);

beforeEach(async () => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'maintain-slim-'));
  dbPath = path.join(tmp, 'db.sqlite');
  process.env.EPISODIC_MEMORY_CONFIG_DIR = path.join(tmp, 'config');
  process.env.TEST_DB_PATH = dbPath;
  process.env.TEST_ARCHIVE_DIR = path.join(tmp, 'archive');
  makeLegacyDb(dbPath);
  (await import('./db.js')).resetDatabaseCache();
});

afterEach(async () => {
  (await import('./db.js')).resetDatabaseCache();
  fs.rmSync(tmp, { recursive: true, force: true });
  delete process.env.EPISODIC_MEMORY_CONFIG_DIR;
  delete process.env.TEST_DB_PATH;
  delete process.env.TEST_ARCHIVE_DIR;
});

describe('slimIndex', () => {
  it('refuses when free disk is below twice the estimate, and changes nothing', async () => {
    const { slimIndex } = await import('./maintain.js');
    const r = await slimIndex({ dbPath, freeBytes: TINY });
    expect(r.action).toBe('refused');
    expect(r.detail).toMatch(/reclaimable \d+ bytes/);
    expect(tables(dbPath)).toContain('tool_calls');
    expect(fs.existsSync(dbPath + '.new')).toBe(false);
    expect(fs.existsSync(dbPath + '.bak')).toBe(false);
  });

  it('dry-run reports the estimate and changes nothing', async () => {
    const { slimIndex } = await import('./maintain.js');
    const r = await slimIndex({ dbPath, freeBytes: BIG, dryRun: true });
    expect(r.action).toBe('would-run');
    expect(r.detail).toMatch(/estimated slim size \d+ bytes/);
    expect(tables(dbPath)).toContain('tool_calls');
    expect(tables(dbPath)).not.toContain('meta');
  });

  it('slims: drops tool_calls, machine and archive-only rows, keeps vectors, swaps atomically, keeps .bak', async () => {
    const { slimIndex } = await import('./maintain.js');
    const r = await slimIndex({ dbPath, freeBytes: BIG, invokedBy: 'cli' });
    expect(r.action).toBe('done');
    expect(tables(dbPath)).not.toContain('tool_calls');
    expect(query(dbPath, `SELECT id, session_class FROM exchanges ORDER BY id`)).toEqual([
      { id: 'k1', session_class: 'unknown' },
      { id: 'k2', session_class: 'unknown' },
      { id: 'k3', session_class: 'brain' },
      { id: 'k4', session_class: 'unknown' },
    ]);
    expect(query(dbPath, `SELECT COUNT(*) AS c FROM vec_exchanges`)).toEqual([{ c: 4 }]);
    expect(query(dbPath, `SELECT id FROM vec_exchanges WHERE embedding MATCH ? AND k = 1`, legacyVector(1))).toEqual([{ id: 'k2' }]);
    expect(query(dbPath, `SELECT value FROM meta WHERE key = 'slim_done'`)).toEqual([{ value: '1' }]);
    expect(query(dbPath, `SELECT name FROM sqlite_master WHERE type = 'index' AND name IN ('idx_archive_path', 'idx_session_class') ORDER BY name`))
      .toEqual([{ name: 'idx_archive_path' }, { name: 'idx_session_class' }]);
    expect(fs.existsSync(dbPath + '.bak')).toBe(true);
    expect(tables(dbPath + '.bak')).toContain('tool_calls');
    expect(fs.existsSync(dbPath + '.new')).toBe(false);
    expect(fs.statSync(dbPath).size).toBeLessThan(fs.statSync(dbPath + '.bak').size);
  });

  it('is idempotent; .bak survives the slim run, --keep-bak and dry runs, and a later verified run (auto included) removes it', async () => {
    const { slimIndex } = await import('./maintain.js');
    await slimIndex({ dbPath, freeBytes: BIG, invokedBy: 'sync' });
    expect(fs.existsSync(dbPath + '.bak')).toBe(true);           // the slim run itself never deletes it
    const kept = await slimIndex({ dbPath, invokedBy: 'cli', keepBackup: true });
    expect(kept.action).toBe('skipped');
    expect(kept.detail).toContain('kept db.sqlite.bak');
    expect(fs.existsSync(dbPath + '.bak')).toBe(true);
    await slimIndex({ dbPath, invokedBy: 'sync', dryRun: true });
    expect(fs.existsSync(dbPath + '.bak')).toBe(true);
    const auto = await slimIndex({ dbPath, invokedBy: 'sync' });
    expect(auto.detail).toContain('removed db.sqlite.bak');
    expect(fs.existsSync(dbPath + '.bak')).toBe(false);
  });

  it('keeps .bak when re-verification of the slim index fails', async () => {
    const { slimIndex } = await import('./maintain.js');
    await slimIndex({ dbPath, freeBytes: BIG, invokedBy: 'sync' });
    const db = new Database(dbPath);
    db.prepare(`UPDATE meta SET value = '999' WHERE key = 'slim_rows'`).run(); // fewer rows than recorded
    db.close();
    const r = await slimIndex({ dbPath, invokedBy: 'sync' });
    expect(r.detail).toContain('verification of the current index failed');
    expect(fs.existsSync(dbPath + '.bak')).toBe(true);
  });

  it('finishes the swap after a crash between the two renames', async () => {
    const { slimIndex } = await import('./maintain.js');
    await expect(slimIndex({ dbPath, freeBytes: BIG, failpoint: 'after-first-rename' })).rejects.toThrow('failpoint after-first-rename');
    expect(fs.existsSync(dbPath)).toBe(false);
    expect(fs.existsSync(dbPath + '.bak')).toBe(true);
    expect(fs.existsSync(dbPath + '.new')).toBe(true);
    const r = await slimIndex({ dbPath, freeBytes: BIG });
    expect(r.action).toBe('done');
    expect(r.detail).toMatch(/resumed/);
    expect(tables(dbPath)).not.toContain('tool_calls');
    expect(fs.existsSync(dbPath + '.new')).toBe(false);
  });

  it('leaves the old index untouched after a crash before the swap, then rebuilds cleanly', async () => {
    const { slimIndex } = await import('./maintain.js');
    await expect(slimIndex({ dbPath, freeBytes: BIG, failpoint: 'after-copy' })).rejects.toThrow('failpoint after-copy');
    expect(tables(dbPath)).toContain('tool_calls');
    expect(fs.existsSync(dbPath + '.new')).toBe(true);
    const r = await slimIndex({ dbPath, freeBytes: BIG });
    expect(r.action).toBe('done');
    expect(fs.existsSync(dbPath + '.new')).toBe(false);
    expect(tables(dbPath)).not.toContain('tool_calls');
  });

  it('drops a tool_calls table that an older bundle recreated after the slim', async () => {
    const { slimIndex } = await import('./maintain.js');
    await slimIndex({ dbPath, freeBytes: BIG });
    const db = new Database(dbPath);
    db.exec(`CREATE TABLE tool_calls (id TEXT PRIMARY KEY, exchange_id TEXT NOT NULL, tool_name TEXT NOT NULL, tool_input TEXT, tool_result TEXT, is_error BOOLEAN DEFAULT 0, timestamp TEXT NOT NULL)`);
    db.close();
    const r = await slimIndex({ dbPath, freeBytes: BIG });
    expect(r.detail).toMatch(/dropped a recreated tool_calls table/);
    expect(tables(dbPath)).not.toContain('tool_calls');
  });

  it('purges machine rows in place when the index never had tool_calls (new install)', async () => {
    fs.rmSync(dbPath, { force: true });
    fs.rmSync(dbPath + '-wal', { force: true });
    fs.rmSync(dbPath + '-shm', { force: true });
    const { initDatabase, insertExchange, resetDatabaseCache } = await import('./db.js');
    const db = initDatabase();
    const base = { timestamp: '2026-10-01T00:00:00.000Z', userMessage: 'u', assistantMessage: 'a', lineStart: 1, lineEnd: 2 };
    insertExchange(db, { ...base, id: 'a1', project: '-Users-alice-Code-app', archivePath: '/a/a1.jsonl' }, new Array(384).fill(0.01));
    insertExchange(db, { ...base, id: 'g1', project: '-Users-alice--ironclaude-grader', archivePath: '/a/g1.jsonl' }, new Array(384).fill(0.01));
    resetDatabaseCache();
    const { slimIndex } = await import('./maintain.js');
    const r = await slimIndex({ dbPath });
    expect(r.action).toBe('done');
    expect(r.detail).toMatch(/removed 1 machine-session rows/);
    expect(query(dbPath, `SELECT id FROM exchanges`)).toEqual([{ id: 'a1' }]);
    expect((await slimIndex({ dbPath })).action).toBe('skipped');
  });

  it('removes a stale partial build before measuring free disk, so it never causes a refusal', async () => {
    const { slimIndex, estimateSlimBytes } = await import('./maintain.js');
    const ro = new Database(dbPath, { readonly: true });
    sqliteVec.load(ro);
    const est = estimateSlimBytes(ro);
    ro.close();
    const partial = 2 * est + 4096;
    fs.writeFileSync(dbPath + '.new', Buffer.alloc(partial));
    // Free disk as the volume reports it: the partial build's bytes come back once it is removed.
    const free = () => (2 * est - 1) + (fs.existsSync(dbPath + '.new') ? 0 : partial);
    const r = await slimIndex({ dbPath, freeBytes: free });
    expect(r.action).toBe('done');
    expect(tables(dbPath)).not.toContain('tool_calls');
  });

  it('dry run leaves a stale partial build in place and counts its bytes as free', async () => {
    const { slimIndex, estimateSlimBytes } = await import('./maintain.js');
    const ro = new Database(dbPath, { readonly: true });
    sqliteVec.load(ro);
    const est = estimateSlimBytes(ro);
    ro.close();
    const partial = 2 * est + 4096;
    fs.writeFileSync(dbPath + '.new', Buffer.alloc(partial));
    const free = () => (2 * est - 1) + (fs.existsSync(dbPath + '.new') ? 0 : partial);
    const r = await slimIndex({ dbPath, freeBytes: free, dryRun: true });
    expect(r.action).toBe('would-run');
    expect(r.detail).toContain(`stale partial build ${partial} bytes would be removed`);
    expect(fs.statSync(dbPath + '.new').size).toBe(partial);
    expect(tables(dbPath)).toContain('tool_calls');
  });
});
