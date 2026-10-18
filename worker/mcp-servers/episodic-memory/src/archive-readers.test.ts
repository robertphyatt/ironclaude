import { describe, it, expect, beforeAll, afterAll } from 'vitest';
import fs from 'fs';
import os from 'os';
import path from 'path';
import zlib from 'zlib';
import { fileURLToPath } from 'url';

const here = path.dirname(fileURLToPath(import.meta.url));
const CODEX_FIXTURE = path.join(here, '__fixtures__', 'codex-rollout-sample.jsonl');
const CLAUDE_LINES = [
  { type: 'user', message: { role: 'user', content: 'First question about KIWI' }, timestamp: '2026-10-01T00:00:00.000Z', sessionId: 's1', cwd: '/Users/alice/Code/app' },
  { type: 'assistant', message: { role: 'assistant', content: [{ type: 'text', text: 'First answer' }] }, timestamp: '2026-10-01T00:00:01.000Z' },
  { type: 'user', message: { role: 'user', content: 'Second question' }, timestamp: '2026-10-01T00:00:02.000Z' },
  { type: 'assistant', message: { role: 'assistant', content: [{ type: 'text', text: 'Second answer' }] }, timestamp: '2026-10-01T00:00:03.000Z' },
].map(l => JSON.stringify(l)).join('\n') + '\n';

let tmp: string;
let claudeLogical: string;
let codexLogical: string;

beforeAll(() => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'archive-readers-'));
  const archive = path.join(tmp, 'archive');
  process.env.EPISODIC_MEMORY_CONFIG_DIR = path.join(tmp, 'config');
  process.env.TEST_DB_PATH = path.join(tmp, 'db.sqlite');
  process.env.TEST_ARCHIVE_DIR = archive;
  claudeLogical = path.join(archive, '-Users-alice-Code-app', '11111111-1111-4111-8111-111111111111.jsonl');
  codexLogical = path.join(archive, '-Users-example-Code-ironclaude', 'rollout-2026-07-18T00-00-00-019f7742-abd8-7c62-af7b-fe07189f1ffd.jsonl');
  fs.mkdirSync(path.dirname(claudeLogical), { recursive: true });
  fs.mkdirSync(path.dirname(codexLogical), { recursive: true });
  // Only the compressed files exist: exactly the post-compaction state.
  fs.writeFileSync(claudeLogical + '.zst', zlib.zstdCompressSync(Buffer.from(CLAUDE_LINES)));
  fs.writeFileSync(codexLogical + '.zst', zlib.zstdCompressSync(fs.readFileSync(CODEX_FIXTURE)));
});

afterAll(() => {
  fs.rmSync(tmp, { recursive: true, force: true });
  delete process.env.EPISODIC_MEMORY_CONFIG_DIR;
  delete process.env.TEST_DB_PATH;
  delete process.env.TEST_ARCHIVE_DIR;
});

describe('readers resolve compacted archive files through the logical path', () => {
  it('parseConversation reads a .jsonl.zst', async () => {
    const { parseConversation } = await import('./parser.js');
    const ex = await parseConversation(claudeLogical, '-Users-alice-Code-app', claudeLogical);
    expect(ex.map(e => e.userMessage)).toEqual(['First question about KIWI', 'Second question']);
    expect(ex[0].archivePath).toBe(claudeLogical);
  });

  it('parseCodexConversation reads a .jsonl.zst', async () => {
    const { parseCodexConversation } = await import('./codex-parser.js');
    const ex = await parseCodexConversation(codexLogical, 'p', codexLogical);
    expect(ex.length).toBe(1);
    expect(ex[0].userMessage).toContain('ZEBRA_MARKER_ingest_test');
  });

  it('formatResults reports the lines and the decompressed size of a compacted file', async () => {
    const { formatResults } = await import('./search.js');
    const out = await formatResults([{
      exchange: { id: 'x', project: 'p', timestamp: '2026-10-01T00:00:00.000Z', userMessage: 'q', assistantMessage: 'a', archivePath: claudeLogical, lineStart: 1, lineEnd: 2 },
      similarity: 0.5, snippet: 'q',
    }]);
    const kb = Math.round(Buffer.byteLength(CLAUDE_LINES) / 1024 * 10) / 10;
    expect(out).toContain(`in ${claudeLogical} (${kb}KB, 4 lines)`);
  });

  it('verifyIndex does not report rows of a compacted file as orphaned', async () => {
    const { initDatabase, insertExchange } = await import('./db.js');
    const db = initDatabase();
    insertExchange(db, {
      id: 'row-1', project: '-Users-alice-Code-app', timestamp: '2026-10-01T00:00:00.000Z',
      userMessage: 'q', assistantMessage: 'a', archivePath: claudeLogical, lineStart: 1, lineEnd: 2,
    }, new Array(384).fill(0.01));
    db.close();
    const { verifyIndex } = await import('./verify.js');
    const result = await verifyIndex();
    expect(result.orphaned).toEqual([]);
    expect(result.missing.map(m => m.path)).toContain(claudeLogical);
  });
});
