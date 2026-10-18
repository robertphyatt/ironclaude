import { describe, it, expect, beforeAll, afterAll } from 'vitest';
import fs from 'fs';
import os from 'os';
import path from 'path';
import zlib from 'zlib';
import { fileURLToPath } from 'url';

const here = path.dirname(fileURLToPath(import.meta.url));
const ASK_FIXTURE = path.join(here, '__fixtures__', 'claude-askuser-session.jsonl');
const CODEX_FIXTURE = path.join(here, '__fixtures__', 'codex-rollout-sample.jsonl');

/** Compress with a frame checksum, then flip the checksum's last byte: the data no longer verifies. */
function damagedZst(raw: Buffer): Buffer {
  const z = zlib.zstdCompressSync(raw, { params: { [zlib.constants.ZSTD_c_checksumFlag]: 1 } });
  z[z.length - 1] ^= 0xff;
  return z;
}

let tmp: string;
let claudeLogical: string;
let codexLogical: string;

beforeAll(() => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'archive-damaged-'));
  const archive = path.join(tmp, 'archive');
  process.env.EPISODIC_MEMORY_CONFIG_DIR = path.join(tmp, 'config');
  process.env.TEST_DB_PATH = path.join(tmp, 'db.sqlite');
  process.env.TEST_ARCHIVE_DIR = archive;
  claudeLogical = path.join(archive, '-Users-alice-Code-app', '11111111-1111-4111-8111-111111111111.jsonl');
  codexLogical = path.join(archive, '-Users-example-Code-ironclaude', 'rollout-2026-07-18T00-00-00-019f7742-abd8-7c62-af7b-fe07189f1ffd.jsonl');
  fs.mkdirSync(path.dirname(claudeLogical), { recursive: true });
  fs.mkdirSync(path.dirname(codexLogical), { recursive: true });
  fs.writeFileSync(claudeLogical + '.zst', damagedZst(fs.readFileSync(ASK_FIXTURE)));
  fs.writeFileSync(codexLogical + '.zst', damagedZst(fs.readFileSync(CODEX_FIXTURE)));
  // Superseded copies sit beside a transcript; they are archived, never indexed (R7).
  fs.writeFileSync(`${claudeLogical}.superseded-1700000000000`, fs.readFileSync(ASK_FIXTURE));
  fs.writeFileSync(`${claudeLogical}.superseded-1700000000001.zst`, zlib.zstdCompressSync(fs.readFileSync(ASK_FIXTURE)));
});

afterAll(() => {
  fs.rmSync(tmp, { recursive: true, force: true });
  delete process.env.EPISODIC_MEMORY_CONFIG_DIR;
  delete process.env.TEST_DB_PATH;
  delete process.env.TEST_ARCHIVE_DIR;
});

describe('a damaged .zst fails fast', () => {
  it('positive control: the fixtures really are damaged', () => {
    expect(() => zlib.zstdDecompressSync(fs.readFileSync(claudeLogical + '.zst'))).toThrow();
    expect(() => zlib.zstdDecompressSync(fs.readFileSync(codexLogical + '.zst'))).toThrow();
  });

  it('parseConversation rejects instead of hanging or returning a partial parse', async () => {
    const { parseConversation } = await import('./parser.js');
    await expect(parseConversation(claudeLogical, '-Users-alice-Code-app', claudeLogical)).rejects.toThrow();
  });

  it('parseCodexConversation rejects instead of hanging or returning a partial parse', async () => {
    const { parseCodexConversation } = await import('./codex-parser.js');
    await expect(parseCodexConversation(codexLogical, 'p', codexLogical)).rejects.toThrow();
  });
});

describe('superseded copies are archived, not indexed', () => {
  it('verifyIndex never lists a superseded copy', async () => {
    const { verifyIndex } = await import('./verify.js');
    const result = await verifyIndex();
    const listed = [
      ...result.missing.map(m => m.path),
      ...result.outdated.map(o => o.path),
      ...result.corrupted.map(c => c.path),
    ];
    expect(listed.some(p => p.includes('.superseded-'))).toBe(false);
    expect(listed).toContain(claudeLogical); // positive control: the transcript beside them IS listed
  });
});
