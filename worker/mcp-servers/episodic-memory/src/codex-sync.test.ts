import { describe, it, expect, beforeAll, afterAll } from 'vitest';
import fs from 'fs';
import os from 'os';
import path from 'path';
import { fileURLToPath } from 'url';

const here = path.dirname(fileURLToPath(import.meta.url));
const FIXTURE = path.join(here, '__fixtures__', 'codex-rollout-sample.jsonl');

let tmp: string;

beforeAll(() => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'codex-ingest-'));
  // Isolate the DB and archive so the test never touches the real index.
  process.env.TEST_DB_PATH = path.join(tmp, 'db.sqlite');
  process.env.TEST_ARCHIVE_DIR = path.join(tmp, 'archive');
  // Build a Codex-shaped source tree with the real fixture.
  const day = path.join(tmp, 'sessions', '2026', '07', '18');
  fs.mkdirSync(day, { recursive: true });
  fs.copyFileSync(
    FIXTURE,
    path.join(day, 'rollout-2026-07-18T00-00-00-019f7742-abd8-7c62-af7b-fe07189f1ffd.jsonl')
  );
});

afterAll(() => {
  fs.rmSync(tmp, { recursive: true, force: true });
  delete process.env.TEST_DB_PATH;
  delete process.env.TEST_ARCHIVE_DIR;
});

describe('syncCodexConversations end-to-end', () => {
  it('ingests a real Codex rollout so search returns it (the requirement)', async () => {
    const { syncCodexConversations } = await import('./codex-sync.js');
    const { searchConversations } = await import('./search.js');
    const dest = path.join(tmp, 'archive');

    // skipSummaries: the acceptance criterion is ingest->search; summaries would
    // invoke the live summarizer with no bearing on this assertion.
    const result = await syncCodexConversations(path.join(tmp, 'sessions'), dest, { skipSummaries: true });
    expect(result.copied).toBeGreaterThan(0);
    expect(result.indexed).toBeGreaterThan(0);
    expect(result.errors).toEqual([]);

    // Text-mode search avoids a query-embedding round-trip; the row was stored above.
    const hits = await searchConversations('ZEBRA_MARKER_ingest_test', { mode: 'text' });
    expect(hits.length).toBeGreaterThan(0);
    expect(hits.some(h => h.exchange.userMessage.includes('ZEBRA_MARKER_ingest_test'))).toBe(true);
    // Grouped under the munged cwd from the fixture's session_meta.
    expect(hits.some(h => h.exchange.project === '-Users-roberthyatt-Code-ironclaude')).toBe(true);
  }, 120000);
});
