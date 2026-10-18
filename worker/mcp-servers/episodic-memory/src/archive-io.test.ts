import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import fs from 'fs';
import os from 'os';
import path from 'path';
import zlib from 'zlib';
import {
  zstdAvailable, logicalPath, resolveArchiveFile, archiveExists, archiveStat,
  openArchiveStream, readArchiveText, archiveNeedsCopy, copyIntoArchive,
} from './archive-io.js';

let tmp: string;
const BODY = '{"type":"user","n":1}\n{"type":"assistant","n":2}\n';

async function streamToString(s: NodeJS.ReadableStream): Promise<string> {
  const chunks: Buffer[] = [];
  for await (const c of s) chunks.push(Buffer.from(c as Buffer));
  return Buffer.concat(chunks).toString('utf-8');
}

beforeEach(() => { tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'archive-io-')); });
afterEach(() => { vi.restoreAllMocks(); fs.rmSync(tmp, { recursive: true, force: true }); });

describe('archive-io', () => {
  it('has built-in zstd on this Node (positive control)', () => {
    expect(zstdAvailable()).toBe(true);
  });

  it('maps .jsonl.zst back to the logical .jsonl path', () => {
    expect(logicalPath('/a/b/s.jsonl.zst')).toBe('/a/b/s.jsonl');
    expect(logicalPath('/a/b/s.jsonl')).toBe('/a/b/s.jsonl');
  });

  it('prefers the raw file, falls back to .zst, and returns null when neither exists', () => {
    const logical = path.join(tmp, 'p', 's.jsonl');
    fs.mkdirSync(path.dirname(logical), { recursive: true });
    expect(resolveArchiveFile(logical)).toBeNull();
    expect(archiveExists(logical)).toBe(false);
    fs.writeFileSync(logical + '.zst', zlib.zstdCompressSync(Buffer.from(BODY)));
    expect(resolveArchiveFile(logical)).toBe(logical + '.zst');
    fs.writeFileSync(logical, BODY);
    expect(resolveArchiveFile(logical)).toBe(logical);
  });

  it('reads compressed text and streams it byte-identically, including nested subagents paths', async () => {
    const logical = path.join(tmp, 'proj', 'sess', 'subagents', 'agent-a1.jsonl');
    fs.mkdirSync(path.dirname(logical), { recursive: true });
    fs.writeFileSync(logical + '.zst', zlib.zstdCompressSync(Buffer.from(BODY)));
    expect(readArchiveText(logical)).toBe(BODY);
    expect(readArchiveText(logical + '.zst')).toBe(BODY);
    expect(await streamToString(openArchiveStream(logical))).toBe(BODY);
    expect(archiveStat(logical)!.size).toBe(fs.statSync(logical + '.zst').size);
  });

  it('throws ENOENT naming the logical path when no file exists', () => {
    const logical = path.join(tmp, 'missing.jsonl');
    expect(() => readArchiveText(logical)).toThrow(/ENOENT: archive file not found: .*missing\.jsonl$/);
    expect(() => openArchiveStream(logical)).toThrow(/ENOENT/);
  });

  it('copies with COPYFILE_FICLONE and skips when the archive copy is current', () => {
    const src = path.join(tmp, 'src.jsonl');
    const dest = path.join(tmp, 'arch', 'proj', 'src.jsonl');
    fs.writeFileSync(src, BODY);
    const spy = vi.spyOn(fs, 'copyFileSync');
    expect(copyIntoArchive(src, dest)).toBe(true);
    expect(fs.readFileSync(dest, 'utf-8')).toBe(BODY);
    expect(spy).toHaveBeenCalledTimes(1);
    expect(spy.mock.calls[0][2]).toBe(fs.constants.COPYFILE_FICLONE);
    expect(copyIntoArchive(src, dest)).toBe(false);
  });

  it('treats a compacted .zst as the archive copy and re-copies only when the source is newer', () => {
    const src = path.join(tmp, 'src.jsonl');
    const dest = path.join(tmp, 'arch', 'src.jsonl');
    fs.mkdirSync(path.dirname(dest), { recursive: true });
    fs.writeFileSync(src, BODY);
    const old = new Date(Date.now() - 3_600_000);
    fs.utimesSync(src, old, old);
    fs.writeFileSync(dest + '.zst', zlib.zstdCompressSync(Buffer.from(BODY)));
    fs.utimesSync(dest + '.zst', old, old);
    expect(archiveNeedsCopy(src, dest)).toBe(false);
    expect(copyIntoArchive(src, dest)).toBe(false);
    expect(fs.existsSync(dest)).toBe(false);
    const newer = new Date();
    fs.utimesSync(src, newer, newer);
    expect(copyIntoArchive(src, dest)).toBe(true);
    expect(fs.readFileSync(dest, 'utf-8')).toBe(BODY);
  });
  it('keeps a larger archive copy beside the new one when the source was rewritten smaller', () => {
    const src = path.join(tmp, 'src.jsonl');
    const dest = path.join(tmp, 'arch', 'src.jsonl');
    fs.mkdirSync(path.dirname(dest), { recursive: true });
    const LARGE = BODY + BODY + BODY;
    fs.writeFileSync(dest, LARGE);
    const old = new Date(Date.now() - 3_600_000);
    fs.utimesSync(dest, old, old);
    const oldMt = Math.trunc(fs.statSync(dest).mtimeMs);
    fs.writeFileSync(src, BODY);
    expect(copyIntoArchive(src, dest)).toBe(true);
    expect(fs.readFileSync(dest, 'utf-8')).toBe(BODY);
    expect(fs.readFileSync(`${dest}.superseded-${oldMt}`, 'utf-8')).toBe(LARGE);
  });

  it('replaces a smaller archive copy (normal growth) without keeping a superseded copy', () => {
    const src = path.join(tmp, 'src.jsonl');
    const dest = path.join(tmp, 'arch', 'src.jsonl');
    fs.mkdirSync(path.dirname(dest), { recursive: true });
    fs.writeFileSync(dest, BODY);
    const old = new Date(Date.now() - 3_600_000);
    fs.utimesSync(dest, old, old);
    const LARGE = BODY + BODY;
    fs.writeFileSync(src, LARGE);
    expect(copyIntoArchive(src, dest)).toBe(true);
    expect(fs.readFileSync(dest, 'utf-8')).toBe(LARGE);
    expect(fs.readdirSync(path.dirname(dest)).filter(n => n.includes('superseded'))).toEqual([]);
  });

  it('refuses to overwrite an existing superseded copy and leaves the archive unchanged', () => {
    const src = path.join(tmp, 'src.jsonl');
    const dest = path.join(tmp, 'arch', 'src.jsonl');
    fs.mkdirSync(path.dirname(dest), { recursive: true });
    const LARGE = BODY + BODY + BODY;
    fs.writeFileSync(dest, LARGE);
    const old = new Date(Date.now() - 3_600_000);
    fs.utimesSync(dest, old, old);
    const keep = `${dest}.superseded-${Math.trunc(fs.statSync(dest).mtimeMs)}`;
    fs.writeFileSync(keep, 'earlier');
    fs.writeFileSync(src, BODY);
    expect(() => copyIntoArchive(src, dest)).toThrow(/refusing to overwrite/);
    expect(fs.readFileSync(dest, 'utf-8')).toBe(LARGE);
    expect(fs.readFileSync(keep, 'utf-8')).toBe('earlier');
  });
});
