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

// A real codex session_meta embeds payload.base_instructions.text — the entire system
// prompt — so line 1 now routinely approaches 64KB. A fixed-buffer read truncated it,
// JSON.parse threw, and the session silently lost BOTH its project grouping and its
// summary. These pin the boundary.
const PAD_MARKER = '@@PAD@@';
const FOUR_BYTE_CHAR = '\u{1F600}'; // 4 UTF-8 bytes

function oversizedMetaLine(
  sessionId: string,
  cwd: string,
  opts: { straddleBoundary: boolean },
): string {
  const template = JSON.stringify({
    timestamp: '2026-07-25T08:46:31.157Z',
    type: 'session_meta',
    payload: { session_id: sessionId, cwd, base_instructions: { text: PAD_MARKER } },
  });
  const markerIndex = template.indexOf(PAD_MARKER);
  const prefixBytes = Buffer.byteLength(template.slice(0, markerIndex), 'utf-8');

  let pad: string;
  if (opts.straddleBoundary) {
    // Land a 4-byte character across byte offset 65536 so a chunk-at-a-time reader
    // that decoded per chunk would corrupt it.
    pad = 'x'.repeat(65536 - prefixBytes - 2) + FOUR_BYTE_CHAR + 'y'.repeat(6000);
  } else {
    pad = 'x'.repeat(70000);
  }
  return template.slice(0, markerIndex) + pad + template.slice(markerIndex + PAD_MARKER.length);
}

function writeRollout(name: string, firstLine: string, extraLines: string[] = []): string {
  const file = path.join(tmp, name);
  fs.writeFileSync(file, [firstLine, ...extraLines].join('\n'), 'utf-8');
  return file;
}

describe('readSessionMeta first-line handling', () => {
  it('recovers session_id and cwd when line 1 exceeds 64KB', async () => {
    const { readSessionMeta } = await import('./codex-sync.js');
    const line = oversizedMetaLine('019f9873-e9b2-78e0-a73a-746b4c26244e', '/Users/x/repo', {
      straddleBoundary: false,
    });
    expect(Buffer.byteLength(line, 'utf-8')).toBeGreaterThan(65536);

    const meta = readSessionMeta(writeRollout('rollout-oversized.jsonl', line, ['{"type":"event_msg"}']));

    expect(meta.sessionId).toBe('019f9873-e9b2-78e0-a73a-746b4c26244e');
    expect(meta.cwd).toBe('/Users/x/repo');
  });

  it('recovers meta when a multi-byte character straddles the 64KB boundary', async () => {
    const { readSessionMeta } = await import('./codex-sync.js');
    const line = oversizedMetaLine('019f0000-0000-7000-8000-000000000001', '/Users/y/repo', {
      straddleBoundary: true,
    });
    expect(Buffer.byteLength(line, 'utf-8')).toBeGreaterThan(65536);

    const meta = readSessionMeta(writeRollout('rollout-multibyte.jsonl', line, ['{"type":"event_msg"}']));

    expect(meta.sessionId).toBe('019f0000-0000-7000-8000-000000000001');
    expect(meta.cwd).toBe('/Users/y/repo');
  });

  it('still reads a small session_meta line', async () => {
    const { readSessionMeta } = await import('./codex-sync.js');
    const line = JSON.stringify({
      type: 'session_meta',
      payload: { session_id: 'small-session', cwd: '/tmp/small' },
    });

    const meta = readSessionMeta(writeRollout('rollout-small.jsonl', line, ['{"type":"event_msg"}']));

    expect(meta.sessionId).toBe('small-session');
    expect(meta.cwd).toBe('/tmp/small');
  });

  it('returns empty for a file whose first line is not session_meta', async () => {
    const { readSessionMeta } = await import('./codex-sync.js');

    const meta = readSessionMeta(
      writeRollout('rollout-nometa.jsonl', JSON.stringify({ type: 'event_msg' })),
    );

    expect(meta).toEqual({});
  });
});
