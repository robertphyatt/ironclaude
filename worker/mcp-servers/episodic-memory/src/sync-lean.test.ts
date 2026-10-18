import { describe, it, expect, beforeAll, afterAll, vi } from 'vitest';
import fs from 'fs';
import os from 'os';
import path from 'path';
import zlib from 'zlib';
import Database from 'better-sqlite3';
import { fileURLToPath } from 'url';

vi.mock('./embeddings.js', () => ({
  initEmbeddings: async () => {},
  generateEmbedding: async () => new Array(384).fill(0.01),
  generateExchangeEmbedding: async () => new Array(384).fill(0.01),
}));
vi.mock('./summarizer.js', () => ({ summarizeConversation: async () => 'SUMMARY' }));

const here = path.dirname(fileURLToPath(import.meta.url));
const CODEX_FIXTURE = path.join(here, '__fixtures__', 'codex-rollout-sample.jsonl');
const S1 = '33333333-3333-4333-8333-333333333333';
const DO_NOT_INDEX = '<INSTRUCTIONS-TO-EPISODIC-MEMORY>DO NOT INDEX THIS CHAT</INSTRUCTIONS-TO-EPISODIC-MEMORY>';

function session(entrypoint: string | undefined, text: string): string {
  const base = entrypoint ? { entrypoint } : {};
  return [
    JSON.stringify({ ...base, type: 'user', message: { role: 'user', content: text }, timestamp: '2026-10-01T00:00:00.000Z' }),
    JSON.stringify({ ...base, type: 'assistant', message: { role: 'assistant', content: [{ type: 'text', text: 'reply' }] }, timestamp: '2026-10-01T00:00:01.000Z' }),
  ].join('\n') + '\n';
}
function write(file: string, body: string) { fs.mkdirSync(path.dirname(file), { recursive: true }); fs.writeFileSync(file, body); }
function rows(archivePrefix: string) {
  const db = new Database(process.env.TEST_DB_PATH!, { readonly: true });
  try {
    return db.prepare(`SELECT archive_path, entrypoint, session_class FROM exchanges WHERE archive_path LIKE ? ORDER BY archive_path`).all(archivePrefix + '%');
  } finally { db.close(); }
}

let tmp: string;
beforeAll(async () => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'sync-lean-'));
  process.env.EPISODIC_MEMORY_CONFIG_DIR = path.join(tmp, 'config');
  process.env.TEST_DB_PATH = path.join(tmp, 'db.sqlite');
  process.env.TEST_ARCHIVE_DIR = path.join(tmp, 'archive');
  const { resetDatabaseCache } = await import('./db.js');
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

describe('sync (lean)', () => {
  it('archives operator sessions and subagents, never archives machine sessions, indexes with provenance', async () => {
    const src = path.join(tmp, 'projects');
    const arch = path.join(tmp, 'arch1');
    const app = path.join(src, '-Users-alice-Code-app');
    write(path.join(app, `${S1}.jsonl`), session('cli', 'LEAN_MARKER operator question'));
    write(path.join(app, S1, 'subagents', 'agent-a1.jsonl'), session('sdk-py', 'LEAN_MARKER subagent work'));
    write(path.join(app, S1, 'tool-results', 'x.txt'), 'tool output');
    write(path.join(app, '44444444-4444-4444-8444-444444444444.jsonl'), session('sdk-cli', 'Output only your exact model id string and nothing else.'));
    write(path.join(src, '-Users-alice--ironclaude-grader', '55555555-5555-4555-8555-555555555555.jsonl'), session('sdk-py', 'LEAN_MARKER grader'));
    write(path.join(src, '-private-tmp', '66666666-6666-4666-8666-666666666666.jsonl'), session(undefined, 'LEAN_MARKER scratch'));
    write(path.join(app, '88888888-8888-4888-8888-888888888888.jsonl'), session('sdk-py', 'Review this change for security vulnerabilities.\n\nChanged files: x'));
    const chunk = '-Users-alice-Code-rp-knowledge-base-programmatically-control-claude-work-chunk-CHUNK-1';
    write(path.join(src, chunk, '99999999-9999-4999-8999-999999999999.jsonl'), session('sdk-py', 'LEAN_MARKER work chunk'));

    const { syncConversations } = await import('./sync.js');
    const r = await syncConversations(src, arch, { skipSummaries: true });
    expect(r.errors).toEqual([]);
    expect(r.excluded).toBe(4);
    // Work-chunk pipeline sessions: archived, never indexed (L16).
    expect(fs.existsSync(path.join(arch, chunk, '99999999-9999-4999-8999-999999999999.jsonl'))).toBe(true);
    const archApp = path.join(arch, '-Users-alice-Code-app');
    expect(fs.existsSync(path.join(archApp, '88888888-8888-4888-8888-888888888888.jsonl'))).toBe(false);
    expect(fs.existsSync(path.join(archApp, `${S1}.jsonl`))).toBe(true);
    expect(fs.existsSync(path.join(archApp, S1, 'subagents', 'agent-a1.jsonl'))).toBe(true);
    expect(fs.existsSync(path.join(archApp, S1, 'tool-results'))).toBe(false);
    expect(fs.existsSync(path.join(archApp, '44444444-4444-4444-8444-444444444444.jsonl'))).toBe(false);
    expect(fs.existsSync(path.join(arch, '-Users-alice--ironclaude-grader'))).toBe(false);
    expect(fs.existsSync(path.join(arch, '-private-tmp'))).toBe(false);
    expect(rows(arch)).toEqual([
      { archive_path: path.join(archApp, `${S1}.jsonl`), entrypoint: 'cli', session_class: 'operator' },
    ]);
  });

  it('does not archive subagent transcripts when exclude.archiveSubagents is false', async () => {
    const src = path.join(tmp, 'projects2');
    const arch = path.join(tmp, 'arch2');
    const app = path.join(src, '-Users-alice-Code-app');
    write(path.join(app, `${S1}.jsonl`), session('cli', 'second'));
    write(path.join(app, S1, 'subagents', 'agent-a1.jsonl'), session('sdk-py', 'sub'));
    write(path.join(tmp, 'config', 'episodic-memory.json'), JSON.stringify({ exclude: { archiveSubagents: false } }));
    try {
      const { syncConversations } = await import('./sync.js');
      await syncConversations(src, arch, { skipSummaries: true, skipIndex: true });
      expect(fs.existsSync(path.join(arch, '-Users-alice-Code-app', `${S1}.jsonl`))).toBe(true);
      expect(fs.existsSync(path.join(arch, '-Users-alice-Code-app', S1, 'subagents'))).toBe(false);
    } finally {
      fs.rmSync(path.join(tmp, 'config', 'episodic-memory.json'), { force: true });
    }
  });

  it('codex: a grader-cwd rollout is not archived; an interactive rollout gets provenance', async () => {
    const day = path.join(tmp, 'sessions', '2026', '07', '18');
    const arch = path.join(tmp, 'arch3');
    const lines = fs.readFileSync(CODEX_FIXTURE, 'utf-8').split('\n');
    write(path.join(day, 'rollout-2026-07-18T00-00-00-019f7742-abd8-7c62-af7b-fe07189f1ffd.jsonl'), lines.join('\n'));
    const meta = JSON.parse(lines[0]);
    meta.payload.cwd = '/Users/alice/.ironclaude/grader';
    meta.payload.session_id = '019f7742-0000-7000-8000-000000000002';
    write(path.join(day, 'rollout-2026-07-18T00-00-01-019f7742-0000-7000-8000-000000000002.jsonl'), [JSON.stringify(meta), ...lines.slice(1)].join('\n'));

    const { syncCodexConversations } = await import('./codex-sync.js');
    const r = await syncCodexConversations(path.join(tmp, 'sessions'), arch, { skipSummaries: true });
    expect(r.errors).toEqual([]);
    expect(r.excluded).toBe(1);
    expect(fs.existsSync(path.join(arch, '-Users-alice--ironclaude-grader'))).toBe(false);
    expect(rows(arch)).toEqual([{
      archive_path: path.join(arch, '-Users-example-Code-ironclaude', 'rollout-2026-07-18T00-00-00-019f7742-abd8-7c62-af7b-fe07189f1ffd.jsonl'),
      entrypoint: 'Codex Desktop', session_class: 'operator',
    }]);
  });

  it('a compacted (.zst-only) archive copy carrying the DO NOT INDEX marker is never summarized', async () => {
    const src = path.join(tmp, 'projects4');
    const arch = path.join(tmp, 'arch4');
    const proj = '-Users-alice-Code-app4';
    const S4 = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
    const body = session('cli', `${DO_NOT_INDEX} private notes`);
    const srcFile = path.join(src, proj, `${S4}.jsonl`);
    write(srcFile, body);
    const old = new Date(Date.now() - 48 * 3_600_000);
    fs.utimesSync(srcFile, old, old);
    const zst = path.join(arch, proj, `${S4}.jsonl.zst`);  // only the compacted copy, newer than the source
    fs.mkdirSync(path.dirname(zst), { recursive: true });
    fs.writeFileSync(zst, zlib.zstdCompressSync(Buffer.from(body)));

    const { syncConversations } = await import('./sync.js');
    const r = await syncConversations(src, arch, {});
    expect(r.errors).toEqual([]);
    expect(r.copied).toBe(0);
    expect(r.summarized).toBe(0);
    expect(fs.existsSync(path.join(arch, proj, `${S4}-summary.txt`))).toBe(false);
  });
});
