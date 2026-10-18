import { describe, it, expect, beforeAll, afterAll, vi } from 'vitest';
import fs from 'fs';
import os from 'os';
import path from 'path';

// The query embedding is the unit vector e0, so distances below are exact.
vi.mock('./embeddings.js', () => ({
  initEmbeddings: async () => {},
  generateEmbedding: async () => Array.from({ length: 384 }, (_, i) => (i === 0 ? 1 : 0)),
  generateExchangeEmbedding: async () => Array.from({ length: 384 }, (_, i) => (i === 0 ? 1 : 0)),
}));

let tmp: string;
const VEC = new Array(384).fill(0.01);
const E0 = Array.from({ length: 384 }, (_, i) => (i === 0 ? 1 : 0));
const E1 = Array.from({ length: 384 }, (_, i) => (i === 1 ? 1 : 0));

beforeAll(async () => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'search-prov-'));
  process.env.EPISODIC_MEMORY_CONFIG_DIR = path.join(tmp, 'config');
  process.env.TEST_DB_PATH = path.join(tmp, 'db.sqlite');
  process.env.TEST_ARCHIVE_DIR = path.join(tmp, 'archive');
  const { initDatabase, insertExchange, resetDatabaseCache } = await import('./db.js');
  resetDatabaseCache();
  const db = initDatabase();
  const row = (id: string, ts: string, text: string, entrypoint?: string, sessionClass?: string, vec: number[] = VEC) =>
    insertExchange(db, {
      id, project: '-Users-alice-Code-app', timestamp: ts, userMessage: text, assistantMessage: 'ok',
      archivePath: `/nonexistent/${id}.jsonl`, lineStart: 1, lineEnd: 2, entrypoint, sessionClass,
    }, vec);
  row('op1', '2026-10-01T00:00:00.000Z', 'KIWI_PROV operator decision', 'cli', 'operator');
  row('wk1', '2026-10-02T00:00:00.000Z', 'KIWI_PROV worker note', 'sdk-py', 'worker');
  row('mc1', '2026-10-03T00:00:00.000Z', 'KIWI_PROV grader chatter', undefined, 'machine');
  row('ao1', '2026-10-04T00:00:00.000Z', 'KIWI_PROV work-chunk pipeline', 'sdk-py', 'archive-only');
  row('legacy1', '2026-09-30T00:00:00.000Z', 'KIWI_PROV legacy row');
  // Twelve worker rows sit exactly on the query vector; one operator row is far away.
  for (let i = 1; i <= 12; i++) row(`vw${i}`, '2026-09-01T00:00:00.000Z', 'VECTOR_ONLY worker', 'sdk-py', 'worker', E0);
  row('vop', '2026-09-01T00:00:00.000Z', 'VECTOR_ONLY operator', 'cli', 'operator', E1);
  resetDatabaseCache();
});

afterAll(async () => {
  const { resetDatabaseCache } = await import('./db.js');
  resetDatabaseCache();
  fs.rmSync(tmp, { recursive: true, force: true });
  delete process.env.EPISODIC_MEMORY_CONFIG_DIR;
  delete process.env.TEST_DB_PATH;
  delete process.env.TEST_ARCHIVE_DIR;
});

describe('search provenance', () => {
  it('excludes machine and archive-only rows, keeps not-yet-classified rows, and returns provenance', async () => {
    const { searchConversations } = await import('./search.js');
    const hits = await searchConversations('KIWI_PROV', { mode: 'text' });
    expect(hits.map(h => h.exchange.id)).toEqual(['wk1', 'op1', 'legacy1']);
    const op = hits.find(h => h.exchange.id === 'op1')!;
    expect(op.exchange.sessionClass).toBe('operator');
    expect(op.exchange.entrypoint).toBe('cli');
  });

  it('sessionClass restricts results to one class', async () => {
    const { searchConversations } = await import('./search.js');
    const hits = await searchConversations('KIWI_PROV', { mode: 'text', sessionClass: 'operator' });
    expect(hits.map(h => h.exchange.id)).toEqual(['op1']);
  });

  it('preferOperator ranks operator sessions first without dropping others', async () => {
    const { searchConversations } = await import('./search.js');
    const hits = await searchConversations('KIWI_PROV', { mode: 'text', preferOperator: true });
    expect(hits.map(h => h.exchange.id)).toEqual(['op1', 'wk1', 'legacy1']);
  });

  it('formatResults shows the session class and entrypoint', async () => {
    const { searchConversations, formatResults } = await import('./search.js');
    const out = await formatResults(await searchConversations('KIWI_PROV', { mode: 'text', sessionClass: 'operator' }));
    expect(out).toContain('   Session: operator (cli)\n');
  });

  it('vector mode over-fetches when restricted to a class, so nearer rows of other classes do not crowd it out', async () => {
    const { searchConversations } = await import('./search.js');
    const hits = await searchConversations('anything', { mode: 'vector', limit: 3, sessionClass: 'operator' });
    expect(hits.map(h => h.exchange.id)).toEqual(['op1', 'vop']);
  });
});
