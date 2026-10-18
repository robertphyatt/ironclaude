import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import fs from 'fs';
import os from 'os';
import path from 'path';
import Database from 'better-sqlite3';
import * as sqliteVec from 'sqlite-vec';

let tmp: string;
const VEC = new Array(384).fill(0.01);
const names = (db: Database.Database, type: string) =>
  (db.prepare(`SELECT name FROM sqlite_master WHERE type = ?`).all(type) as Array<{ name: string }>).map(r => r.name);
const columns = (db: Database.Database) =>
  (db.prepare(`SELECT name FROM pragma_table_info('exchanges')`).all() as Array<{ name: string }>).map(r => r.name);

beforeEach(async () => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'db-lean-'));
  process.env.EPISODIC_MEMORY_CONFIG_DIR = path.join(tmp, 'config');
  process.env.TEST_DB_PATH = path.join(tmp, 'db.sqlite');
  process.env.TEST_ARCHIVE_DIR = path.join(tmp, 'archive');
  const mod: any = await import('./db.js');
  mod.resetDatabaseCache?.();
});

afterEach(async () => {
  const mod: any = await import('./db.js');
  mod.resetDatabaseCache?.();
  fs.rmSync(tmp, { recursive: true, force: true });
  delete process.env.EPISODIC_MEMORY_CONFIG_DIR;
  delete process.env.TEST_DB_PATH;
  delete process.env.TEST_ARCHIVE_DIR;
});

describe('db schema (lean)', () => {
  it('creates no tool_calls table or tool indexes, and has meta, provenance columns and indexes', async () => {
    const { initDatabase } = await import('./db.js');
    const db = initDatabase();
    expect(names(db, 'table')).not.toContain('tool_calls');
    expect(names(db, 'index')).not.toContain('idx_tool_name');
    expect(names(db, 'index')).not.toContain('idx_tool_exchange');
    expect(names(db, 'table')).toContain('meta');
    expect(columns(db)).toEqual(expect.arrayContaining(['entrypoint', 'session_class']));
    expect(names(db, 'index')).toEqual(expect.arrayContaining(['idx_session_class', 'idx_archive_path']));
  });

  it('insertExchange stores entrypoint and session_class and writes no tool rows', async () => {
    const { initDatabase, insertExchange } = await import('./db.js');
    const db = initDatabase();
    insertExchange(db, {
      id: 'e1', project: 'p', timestamp: '2026-10-01T00:00:00.000Z', userMessage: 'u', assistantMessage: 'a',
      archivePath: '/x.jsonl', lineStart: 1, lineEnd: 2, entrypoint: 'cli', sessionClass: 'operator',
      toolCalls: [{ id: 't1', exchangeId: 'e1', toolName: 'Write', toolInput: { big: 'x'.repeat(1000) }, isError: false, timestamp: '2026-10-01T00:00:00.000Z' }],
    } as any, VEC, ['Write']);
    expect(db.prepare(`SELECT entrypoint, session_class FROM exchanges WHERE id = 'e1'`).get())
      .toEqual({ entrypoint: 'cli', session_class: 'operator' });
    expect(names(db, 'table')).not.toContain('tool_calls');
  });

  it('migrates a legacy exchanges table additively and keeps its rows', async () => {
    const legacy = new Database(process.env.TEST_DB_PATH!);
    sqliteVec.load(legacy);
    legacy.exec(`CREATE TABLE exchanges (id TEXT PRIMARY KEY, project TEXT NOT NULL, timestamp TEXT NOT NULL, user_message TEXT NOT NULL, assistant_message TEXT NOT NULL, archive_path TEXT NOT NULL, line_start INTEGER NOT NULL, line_end INTEGER NOT NULL, embedding BLOB)`);
    legacy.prepare(`INSERT INTO exchanges VALUES ('old1','p','2026-01-01T00:00:00.000Z','u','a','/x.jsonl',1,2,NULL)`).run();
    legacy.close();
    const { initDatabase } = await import('./db.js');
    const db = initDatabase();
    expect(columns(db)).toEqual(expect.arrayContaining(['session_id', 'entrypoint', 'session_class']));
    expect(db.prepare(`SELECT COUNT(*) AS c FROM exchanges`).get()).toEqual({ c: 1 });
    // Startup stays cheap on an existing index: maintain builds these under the lock.
    expect(names(db, 'index')).not.toContain('idx_archive_path');
    expect(names(db, 'index')).not.toContain('idx_session_class');
  });

  it('meta helpers: null before the table exists, upsert after', async () => {
    const mod: any = await import('./db.js');
    const raw = new Database(path.join(tmp, 'plain.sqlite'));
    expect(mod.getMeta(raw, 'slim_done')).toBeNull();
    mod.setMeta(raw, 'slim_done', '1');
    mod.setMeta(raw, 'slim_done', '2');
    expect(mod.getMeta(raw, 'slim_done')).toBe('2');
    expect(mod.tableExists(raw, 'meta')).toBe(true);
    raw.close();
  });
});
