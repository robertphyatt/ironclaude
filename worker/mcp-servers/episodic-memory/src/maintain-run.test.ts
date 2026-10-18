import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import fs from 'fs';
import os from 'os';
import path from 'path';
import crypto from 'crypto';
import zlib from 'zlib';
import Database from 'better-sqlite3';
import * as sqliteVec from 'sqlite-vec';
import { fileURLToPath } from 'url';
import { makeLegacyDb } from './__fixtures__/make-legacy-db.js';
import { DEFAULT_SETTINGS } from './settings.js';

vi.mock('./embeddings.js', () => ({
  initEmbeddings: async () => {},
  generateEmbedding: async () => new Array(384).fill(0.01),
  generateExchangeEmbedding: async () => new Array(384).fill(0.01),
}));

const here = path.dirname(fileURLToPath(import.meta.url));
const ASK_FIXTURE = path.join(here, '__fixtures__', 'claude-askuser-session.jsonl');
const VEC = new Array(384).fill(0.01);
let tmp: string;
let dbPath: string;
let archive: string;

function query(sql: string, ...params: unknown[]): any[] {
  const db = new Database(dbPath, { readonly: true });
  sqliteVec.load(db);
  try { return db.prepare(sql).all(...params); } finally { db.close(); }
}

beforeEach(async () => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'maintain-run-'));
  dbPath = path.join(tmp, 'db.sqlite');
  archive = path.join(tmp, 'archive');
  process.env.EPISODIC_MEMORY_CONFIG_DIR = path.join(tmp, 'config');
  process.env.TEST_DB_PATH = dbPath;
  process.env.TEST_ARCHIVE_DIR = archive;
  (await import('./db.js')).resetDatabaseCache();
});
afterEach(async () => {
  (await import('./db.js')).resetDatabaseCache();
  fs.rmSync(tmp, { recursive: true, force: true });
  delete process.env.EPISODIC_MEMORY_CONFIG_DIR;
  delete process.env.TEST_DB_PATH;
  delete process.env.TEST_ARCHIVE_DIR;
});

async function seedPreLeanRows(): Promise<{ askPath: string; probePath: string; oldId: string }> {
  const app = path.join(archive, '-Users-alice-Code-app');
  fs.mkdirSync(app, { recursive: true });
  const askPath = path.join(app, '22222222-2222-4222-8222-222222222222.jsonl');
  fs.copyFileSync(ASK_FIXTURE, askPath);
  const probePath = path.join(app, '77777777-7777-4777-8777-777777777777.jsonl');
  fs.writeFileSync(probePath, [
    JSON.stringify({ entrypoint: 'sdk-cli', type: 'user', message: { role: 'user', content: 'Output only your exact model id string and nothing else.' }, timestamp: '2026-10-01T00:00:00.000Z' }),
    JSON.stringify({ entrypoint: 'sdk-cli', type: 'assistant', message: { role: 'assistant', content: [{ type: 'text', text: 'claude-x' }] }, timestamp: '2026-10-01T00:00:01.000Z' }),
  ].join('\n') + '\n');
  // Rows as the pre-lean parser wrote them: the answer was skipped, so one exchange spans lines 2-5.
  const oldId = crypto.createHash('md5').update(`${askPath}:2-5`).digest('hex');
  const { initDatabase, insertExchange, resetDatabaseCache } = await import('./db.js');
  const db = initDatabase();
  const base = { project: '-Users-alice-Code-app', timestamp: '2026-10-01T00:00:04.000Z' };
  insertExchange(db, { ...base, id: oldId, userMessage: 'Plan the KIWI migration', assistantMessage: 'I need one decision first.\n\nProceeding with retrieval only.', archivePath: askPath, lineStart: 2, lineEnd: 5 }, VEC);
  insertExchange(db, { ...base, id: 'probe-row', userMessage: 'Output only your exact model id string and nothing else.', assistantMessage: 'claude-x', archivePath: probePath, lineStart: 1, lineEnd: 2 }, VEC);
  db.exec(`DROP INDEX idx_archive_path`); // an existing index never got it at startup
  resetDatabaseCache();
  return { askPath, probePath, oldId };
}

describe('backfillVoice', () => {
  it('re-splits sessions with answers into Q→A exchanges, stamps provenance, removes machine sessions, once', async () => {
    const { askPath, oldId } = await seedPreLeanRows();
    const { backfillVoice } = await import('./maintain.js');
    const r = await backfillVoice({});
    expect(r.action).toBe('done');
    const rows = query(`SELECT id, user_message, entrypoint, session_class FROM exchanges ORDER BY line_start`);
    expect(rows.map(x => x.user_message)).toEqual(['Plan the KIWI migration', 'Q: Which scope?\nA: Retrieval only']);
    expect(rows.every(x => x.entrypoint === 'cli' && x.session_class === 'operator')).toBe(true);
    expect(rows.map(x => x.id)).not.toContain(oldId);
    expect(query(`SELECT COUNT(*) AS c FROM vec_exchanges`)).toEqual([{ c: 2 }]);
    expect(query(`SELECT value FROM meta WHERE key = 'voice_backfill_done'`)).toEqual([{ value: '1' }]);
    expect(query(`SELECT name FROM sqlite_master WHERE type = 'index' AND name = 'idx_archive_path'`)).toEqual([{ name: 'idx_archive_path' }]);
    expect(fs.existsSync(askPath)).toBe(true);
    expect((await backfillVoice({})).action).toBe('skipped');
  });

  it('dry-run changes nothing', async () => {
    await seedPreLeanRows();
    const { backfillVoice } = await import('./maintain.js');
    const r = await backfillVoice({ dryRun: true });
    expect(r.action).toBe('would-run');
    expect(r.detail).toContain('2 sessions');
    expect(query(`SELECT COUNT(*) AS c FROM exchanges`)).toEqual([{ c: 2 }]);
    expect(query(`SELECT COUNT(*) AS c FROM exchanges WHERE session_class IS NOT NULL`)).toEqual([{ c: 0 }]);
  });

  it('a later slim run still verifies and removes .bak after the backfill removed machine rows', async () => {
    makeLegacyDb(dbPath, archive);
    const k1 = path.join(archive, '-Users-alice-Code-app', 'k1.jsonl');
    fs.mkdirSync(path.dirname(k1), { recursive: true });
    fs.writeFileSync(k1, [
      JSON.stringify({ entrypoint: 'sdk-cli', type: 'user', message: { role: 'user', content: 'Output only your exact model id string and nothing else.' }, timestamp: '2026-10-01T00:00:00.000Z' }),
      JSON.stringify({ entrypoint: 'sdk-cli', type: 'assistant', message: { role: 'assistant', content: [{ type: 'text', text: 'claude-x' }] }, timestamp: '2026-10-01T00:00:01.000Z' }),
    ].join('\n') + '\n');
    const { slimIndex, backfillVoice } = await import('./maintain.js');
    expect((await slimIndex({ dbPath, freeBytes: () => 1e15, invokedBy: 'sync' })).action).toBe('done');
    expect(query(`SELECT value FROM meta WHERE key = 'slim_rows'`)).toEqual([{ value: '4' }]);
    const b = await backfillVoice({});
    expect(b.detail).toContain('machine sessions removed from the index 1');
    expect(query(`SELECT COUNT(*) AS c FROM exchanges`)).toEqual([{ c: 3 }]);
    const again = await slimIndex({ dbPath, invokedBy: 'sync' });
    expect(again.detail).toContain('removed db.sqlite.bak');
    expect(fs.existsSync(dbPath + '.bak')).toBe(false);
  });

  it('treats a session whose transcript cannot be read as unreadable: keeps its rows and finishes the rest', async () => {
    const { askPath } = await seedPreLeanRows();
    const bad = path.join(path.dirname(askPath), '11111111-1111-4111-8111-111111111111.jsonl'); // sorts before the good session
    const body = zlib.zstdCompressSync(fs.readFileSync(ASK_FIXTURE), { params: { [zlib.constants.ZSTD_c_checksumFlag]: 1 } });
    body[body.length - 1] ^= 0xff; // the frame checksum no longer matches
    expect(() => zlib.zstdDecompressSync(body)).toThrow(); // positive control: the fixture really is damaged
    fs.writeFileSync(bad + '.zst', body);
    const { initDatabase, insertExchange, resetDatabaseCache } = await import('./db.js');
    const db = initDatabase();
    insertExchange(db, { id: 'bad-row', project: '-Users-alice-Code-app', timestamp: '2026-10-01T00:00:00.000Z', userMessage: 'kept as is', assistantMessage: 'a', archivePath: bad, lineStart: 1, lineEnd: 2 }, VEC);
    resetDatabaseCache();
    const { backfillVoice } = await import('./maintain.js');
    const r = await backfillVoice({});
    expect(r.action).toBe('done');
    expect(r.detail).toContain('unreadable archive files 1');
    expect(query(`SELECT user_message FROM exchanges WHERE id = 'bad-row'`)).toEqual([{ user_message: 'kept as is' }]);
    expect(query(`SELECT COUNT(*) AS c FROM exchanges WHERE user_message LIKE 'Q: %'`)).toEqual([{ c: 1 }]);
  });

  it('skips without writing when the index has not been slimmed (tool_calls still present)', async () => {
    makeLegacyDb(dbPath, archive);
    const { runMaintain } = await import('./maintain.js');
    const reports = await runMaintain({ only: 'backfill', archiveDir: archive });
    expect(reports.map(r => [r.phase, r.action])).toEqual([['backfill', 'skipped']]);
    expect(reports[0].detail).toContain('has not been slimmed yet');
    const tables = query(`SELECT name FROM sqlite_master WHERE type = 'table'`).map(r => r.name);
    expect(tables).toContain('tool_calls');
    expect(tables).not.toContain('meta');
    expect(query(`SELECT name FROM pragma_table_info('exchanges') WHERE name = 'session_class'`)).toEqual([]);
  });

  it('--only=backfill --dry-run on an index that has not been slimmed reports skipped, like the real run', async () => {
    makeLegacyDb(dbPath, archive);
    const { runMaintain } = await import('./maintain.js');
    const reports = await runMaintain({ only: 'backfill', dryRun: true, archiveDir: archive });
    expect(reports.map(r => [r.phase, r.action])).toEqual([['backfill', 'skipped']]);
    expect(reports[0].detail).toContain('has not been slimmed yet');
    const tables = query(`SELECT name FROM sqlite_master WHERE type = 'table'`).map(r => r.name);
    expect(tables).toContain('tool_calls');
    expect(tables).not.toContain('meta');
  });
});

describe('runMaintain and autoRunAllowed', () => {
  it('--only runs one phase', async () => {
    const { runMaintain } = await import('./maintain.js');
    const reports = await runMaintain({ only: 'compact', archiveDir: archive });
    expect(reports.map(r => r.phase)).toEqual(['compact']);
  });

  it('a dry-run over a legacy index writes nothing', async () => {
    makeLegacyDb(dbPath);
    const { runMaintain } = await import('./maintain.js');
    const reports = await runMaintain({ dryRun: true, archiveDir: archive, freeBytes: () => 1e15 });
    expect(reports.map(r => [r.phase, r.action])).toEqual([
      ['slim', 'would-run'], ['backfill', 'would-run'], ['compact', 'skipped'], ['mirror', 'skipped'],
    ]);
    const tables = query(`SELECT name FROM sqlite_master WHERE type = 'table'`).map(r => r.name);
    expect(tables).toContain('tool_calls');
    expect(tables).not.toContain('meta');
    expect(query(`SELECT name FROM pragma_table_info('exchanges') WHERE name = 'session_class'`)).toEqual([]);
  });

  it('an automatic run on a legacy index with too little disk refuses the slim and leaves the index untouched', async () => {
    makeLegacyDb(dbPath);
    const { runMaintain } = await import('./maintain.js');
    const reports = await runMaintain({ invokedBy: 'sync', archiveDir: archive, freeBytes: () => 1 });
    expect(reports.map(r => [r.phase, r.action])).toEqual([
      ['slim', 'refused'], ['backfill', 'skipped'], ['compact', 'skipped'], ['mirror', 'skipped'],
    ]);
    const tables = query(`SELECT name FROM sqlite_master WHERE type = 'table'`).map(r => r.name);
    expect(tables).toContain('tool_calls');
    expect(tables).not.toContain('meta');
    expect(fs.existsSync(dbPath + '.new')).toBe(false);
  });

  it('autoRunAllowed: on by default for every install, legacy-bloated included (L16); autoRun false opts out', async () => {
    const { autoRunAllowed } = await import('./maintain.js');
    const off = { ...DEFAULT_SETTINGS, maintain: { ...DEFAULT_SETTINGS.maintain, autoRun: false } };
    expect(autoRunAllowed(DEFAULT_SETTINGS)).toEqual({ allowed: true, reason: 'maintain.autoRun is true' });
    makeLegacyDb(dbPath);
    expect(autoRunAllowed(DEFAULT_SETTINGS)).toEqual({ allowed: true, reason: 'maintain.autoRun is true' });
    expect(autoRunAllowed(off)).toEqual({ allowed: false, reason: 'maintain.autoRun is false' });
    fs.mkdirSync(path.join(tmp, 'config'), { recursive: true });
    fs.writeFileSync(path.join(tmp, 'config', 'episodic-memory.json'), JSON.stringify({ maintain: { autoRun: false } }));
    const { loadSettings } = await import('./settings.js');
    expect(autoRunAllowed(loadSettings()).allowed).toBe(false);   // the operator's opt-out file
  });
});
