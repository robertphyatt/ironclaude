import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import fs from 'fs';
import os from 'os';
import path from 'path';
import crypto from 'crypto';
import zlib from 'zlib';
import { execFileSync } from 'child_process';
import { readArchiveText } from './archive-io.js';
import { DEFAULT_SETTINGS } from './settings.js';

const OLD = Date.now() - 48 * 3_600_000;
const BODY = Array.from({ length: 200 }, (_, i) => JSON.stringify({ type: 'user', i, text: 'lorem ipsum '.repeat(10) })).join('\n') + '\n';
const sha = (s: string | Buffer) => crypto.createHash('sha256').update(s).digest('hex');
let tmp: string;
let archive: string;

function writeRaw(rel: string, body = BODY, mtimeMs = OLD): string {
  const f = path.join(archive, rel);
  fs.mkdirSync(path.dirname(f), { recursive: true });
  fs.writeFileSync(f, body);
  fs.utimesSync(f, mtimeMs / 1000, mtimeMs / 1000);
  return f;
}
const manifestLines = () => {
  const f = path.join(archive, 'manifest.jsonl');
  return fs.existsSync(f) ? fs.readFileSync(f, 'utf-8').trim().split('\n').filter(Boolean).map(l => JSON.parse(l)) : [];
};

beforeEach(() => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'maintain-compact-'));
  archive = path.join(tmp, 'archive');
  fs.mkdirSync(archive, { recursive: true });
  process.env.EPISODIC_MEMORY_CONFIG_DIR = path.join(tmp, 'config');
  process.env.TEST_DB_PATH = path.join(tmp, 'db.sqlite');
  process.env.TEST_ARCHIVE_DIR = archive;
});
afterEach(() => {
  fs.rmSync(tmp, { recursive: true, force: true });
  delete process.env.EPISODIC_MEMORY_CONFIG_DIR;
  delete process.env.TEST_DB_PATH;
  delete process.env.TEST_ARCHIVE_DIR;
});

describe('compactArchive', () => {
  it('compacts an old file losslessly: manifest line, raw removed, zstd -d restores byte-identical', async () => {
    const { compactArchive } = await import('./maintain.js');
    const f = writeRaw('-Users-alice-Code-app/s1.jsonl');
    const summary = path.join(archive, '-Users-alice-Code-app', 's1-summary.txt');
    fs.writeFileSync(summary, 'summary');
    const r = await compactArchive({ archiveDir: archive });
    expect(r.action).toBe('done');
    expect(r.detail).toContain('compacted 1');
    expect(fs.existsSync(f)).toBe(false);
    expect(fs.existsSync(f + '.zst')).toBe(true);
    expect(fs.readFileSync(summary, 'utf-8')).toBe('summary');
    expect(fs.existsSync(summary + '.zst')).toBe(false);
    const [entry, ...rest] = manifestLines();
    expect(rest).toEqual([]);
    expect(entry.path).toBe(path.join('-Users-alice-Code-app', 's1.jsonl'));
    expect(entry.sha256).toBe(sha(BODY));
    expect(entry.rawBytes).toBe(Buffer.byteLength(BODY));
    expect(entry.sessionClass).toBe('unknown');
    const restored = execFileSync('zstd', ['-d', '-c', f + '.zst']);
    expect(Buffer.compare(restored, Buffer.from(BODY))).toBe(0);
    expect(readArchiveText(f)).toBe(BODY);
    expect(Math.abs(fs.statSync(f + '.zst').mtimeMs - OLD)).toBeLessThan(1000);
  });

  it('skips a live file (modified inside the live window)', async () => {
    const { compactArchive } = await import('./maintain.js');
    const f = writeRaw('-Users-alice-Code-app/live.jsonl', BODY, Date.now());
    const r = await compactArchive({ archiveDir: archive });
    expect(r.action).toBe('skipped');
    expect(fs.existsSync(f)).toBe(true);
    expect(fs.existsSync(f + '.zst')).toBe(false);
  });

  it('keeps the raw file and logs an error when the round-trip check fails', async () => {
    const { compactArchive } = await import('./maintain.js');
    const f = writeRaw('-Users-alice-Code-app/s1.jsonl');
    const r = await compactArchive({ archiveDir: archive, failpoint: 'corrupt-compressed' });
    expect(r.detail).toContain('errors 1');
    expect(fs.readFileSync(f, 'utf-8')).toBe(BODY);
    expect(fs.existsSync(f + '.zst')).toBe(false);
    expect(fs.existsSync(f + '.zst.tmp')).toBe(false);
    expect(manifestLines()).toEqual([]);
    const errors = fs.readFileSync(path.join(archive, 'manifest-errors.jsonl'), 'utf-8').trim().split('\n');
    expect(errors.length).toBe(1);
    expect(JSON.parse(errors[0]).path).toBe(path.join('-Users-alice-Code-app', 's1.jsonl'));
  });

  it('is idempotent: a second run is a no-op', async () => {
    const { compactArchive } = await import('./maintain.js');
    writeRaw('-Users-alice-Code-app/s1.jsonl');
    await compactArchive({ archiveDir: archive });
    const before = fs.readFileSync(path.join(archive, 'manifest.jsonl'), 'utf-8');
    const r = await compactArchive({ archiveDir: archive });
    expect(r.action).toBe('skipped');
    expect(fs.readFileSync(path.join(archive, 'manifest.jsonl'), 'utf-8')).toBe(before);
  });

  it('finishes an interrupted removal using the manifest', async () => {
    const { compactArchive } = await import('./maintain.js');
    const f = writeRaw('-Users-alice-Code-app/s1.jsonl');
    await compactArchive({ archiveDir: archive });
    writeRaw('-Users-alice-Code-app/s1.jsonl');  // the raw file is back, as if the unlink never ran
    const r = await compactArchive({ archiveDir: archive });
    expect(r.detail).toContain('resumed 1');
    expect(fs.existsSync(f)).toBe(false);
    expect(manifestLines().length).toBe(1);
  });

  it('never trusts a damaged .zst on resume: re-verifies, recompresses, and only then removes the raw', async () => {
    const { compactArchive } = await import('./maintain.js');
    const f = writeRaw('-Users-alice-Code-app/s1.jsonl');
    await compactArchive({ archiveDir: archive });
    writeRaw('-Users-alice-Code-app/s1.jsonl');  // the raw file is back, as if the unlink never ran
    const damaged = fs.readFileSync(f + '.zst');
    damaged[Math.floor(damaged.length / 2)] ^= 0xff;  // the .zst was damaged between runs
    fs.writeFileSync(f + '.zst', damaged);
    const r = await compactArchive({ archiveDir: archive });
    expect(r.detail).not.toContain('resumed 1');
    expect(r.detail).toContain('compacted 1');
    expect(fs.existsSync(f)).toBe(false);
    const restored = execFileSync('zstd', ['-d', '-c', f + '.zst']);
    expect(Buffer.compare(restored, Buffer.from(BODY))).toBe(0);
  });

  it('compacts nested subagent transcripts, and readers still resolve them', async () => {
    const { compactArchive } = await import('./maintain.js');
    const f = writeRaw('-Users-alice-Code-app/s1/subagents/agent-a1.jsonl');
    await compactArchive({ archiveDir: archive });
    expect(fs.existsSync(f)).toBe(false);
    expect(readArchiveText(f)).toBe(BODY);
  });

  it('tags machine-class files in the manifest (kept, compressed)', async () => {
    const { compactArchive } = await import('./maintain.js');
    writeRaw('-Users-alice--ironclaude-grader/g1.jsonl');
    await compactArchive({ archiveDir: archive });
    expect(manifestLines()[0].sessionClass).toBe('machine');
  });

  it('compacts and tags archive-only work-chunk sessions', async () => {
    const { compactArchive } = await import('./maintain.js');
    const f = writeRaw('-Users-alice-Code-rp-knowledge-base-programmatically-control-claude-work-chunk-CHUNK-1/c1.jsonl');
    await compactArchive({ archiveDir: archive });
    expect(fs.existsSync(f + '.zst')).toBe(true);
    expect(manifestLines()[0].sessionClass).toBe('archive-only');
  });

  it('dry-run reports and changes nothing', async () => {
    const { compactArchive } = await import('./maintain.js');
    const f = writeRaw('-Users-alice-Code-app/s1.jsonl');
    const r = await compactArchive({ archiveDir: archive, dryRun: true });
    expect(r.action).toBe('would-run');
    expect(r.detail).toContain('would compact 1 files');
    expect(fs.existsSync(f)).toBe(true);
  });

  it('keeps a larger compacted copy when the source was rewritten smaller (superseded, never overwritten)', async () => {
    const { compactArchive } = await import('./maintain.js');
    const f = writeRaw('-Users-alice-Code-app/s1.jsonl');
    await compactArchive({ archiveDir: archive });
    const oldMt = Math.trunc(fs.statSync(f + '.zst').mtimeMs);
    const SMALL = '{"type":"user","text":"rewritten"}\n';
    writeRaw('-Users-alice-Code-app/s1.jsonl', SMALL, OLD + 60_000);
    const r = await compactArchive({ archiveDir: archive });
    expect(r.detail).toContain('compacted 1');
    expect(execFileSync('zstd', ['-d', '-c', f + '.zst']).toString()).toBe(SMALL);
    const kept = `${f}.superseded-${oldMt}.zst`;
    expect(Buffer.compare(execFileSync('zstd', ['-d', '-c', kept]), Buffer.from(BODY))).toBe(0);
    const rel = path.join('-Users-alice-Code-app', `s1.jsonl.superseded-${oldMt}`);
    expect(manifestLines().find(l => l.path === rel)).toMatchObject({ sha256: sha(BODY), rawBytes: Buffer.byteLength(BODY) });
  });

  it('normal growth replaces the compacted copy with no superseded file', async () => {
    const { compactArchive } = await import('./maintain.js');
    const f = writeRaw('-Users-alice-Code-app/s1.jsonl');
    await compactArchive({ archiveDir: archive });
    const GROWN = BODY + '{"type":"user","text":"one more"}\n';
    writeRaw('-Users-alice-Code-app/s1.jsonl', GROWN, OLD + 60_000);
    await compactArchive({ archiveDir: archive });
    expect(execFileSync('zstd', ['-d', '-c', f + '.zst']).toString()).toBe(GROWN);
    expect(fs.readdirSync(path.dirname(f)).filter(n => n.includes('superseded'))).toEqual([]);
  });

  it('keeps an old compacted copy that no longer decodes instead of overwriting it', async () => {
    const { compactArchive } = await import('./maintain.js');
    const f = writeRaw('-Users-alice-Code-app/s1.jsonl');
    await compactArchive({ archiveDir: archive });
    const damaged = fs.readFileSync(f + '.zst');
    damaged[Math.floor(damaged.length / 2)] ^= 0xff;
    expect(() => zlib.zstdDecompressSync(damaged)).toThrow(); // positive control: it really no longer decodes
    fs.writeFileSync(f + '.zst', damaged);
    fs.utimesSync(f + '.zst', OLD / 1000, OLD / 1000);
    const oldMt = Math.trunc(fs.statSync(f + '.zst').mtimeMs);
    const GROWN = BODY + '{"type":"user","text":"one more"}\n';
    writeRaw('-Users-alice-Code-app/s1.jsonl', GROWN, OLD + 60_000);
    await compactArchive({ archiveDir: archive });
    expect(Buffer.compare(fs.readFileSync(`${f}.superseded-${oldMt}.zst`), damaged)).toBe(0);
    expect(execFileSync('zstd', ['-d', '-c', f + '.zst']).toString()).toBe(GROWN);
  });

  it('compacts a superseded raw copy losslessly and records it in the manifest', async () => {
    const { compactArchive } = await import('./maintain.js');
    const f = writeRaw('-Users-alice-Code-app/s1.jsonl.superseded-1700000000000');
    const r = await compactArchive({ archiveDir: archive });
    expect(r.detail).toContain('compacted 1');
    expect(fs.existsSync(f)).toBe(false);
    expect(Buffer.compare(execFileSync('zstd', ['-d', '-c', f + '.zst']), Buffer.from(BODY))).toBe(0);
    expect(manifestLines().map(l => l.path)).toEqual([path.join('-Users-alice-Code-app', 's1.jsonl.superseded-1700000000000')]);
  });
});

describe('mirrorBackup', () => {
  it('copies verified .zst files; a second run finds them current', async () => {
    const { compactArchive, mirrorBackup } = await import('./maintain.js');
    const f = writeRaw('-Users-alice-Code-app/s1.jsonl');
    await compactArchive({ archiveDir: archive });
    const mirrorDir = path.join(tmp, 'mirror');
    const settings = { ...DEFAULT_SETTINGS, backup: { mirrorDir } };
    const r = await mirrorBackup({ archiveDir: archive, settings });
    expect(r.detail).toContain('mirrored 1, failed 0');
    const mirrored = path.join(mirrorDir, '-Users-alice-Code-app', 's1.jsonl.zst');
    expect(Buffer.compare(fs.readFileSync(mirrored), fs.readFileSync(f + '.zst'))).toBe(0);
    const r2 = await mirrorBackup({ archiveDir: archive, settings });
    expect(r2.detail).toContain('mirrored 0, failed 0, already current 1');
  });

  it('fails soft when the mirror directory is unavailable', async () => {
    const { compactArchive, mirrorBackup } = await import('./maintain.js');
    writeRaw('-Users-alice-Code-app/s1.jsonl');
    await compactArchive({ archiveDir: archive });
    const blocker = path.join(tmp, 'blocker');
    fs.writeFileSync(blocker, 'a regular file, not a directory');
    const r = await mirrorBackup({ archiveDir: archive, settings: { ...DEFAULT_SETTINGS, backup: { mirrorDir: path.join(blocker, 'sub') } } });
    expect(r.action).toBe('skipped');
    expect(r.detail).toMatch(/backup mirror unavailable/);
  });

  it('is off by default', async () => {
    const { mirrorBackup } = await import('./maintain.js');
    const r = await mirrorBackup({ archiveDir: archive, settings: DEFAULT_SETTINGS });
    expect(r).toEqual({ phase: 'mirror', action: 'skipped', detail: 'no backup.mirrorDir configured' });
  });
});
