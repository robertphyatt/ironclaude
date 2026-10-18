/**
 * archive-io.ts — the one way to read and copy conversation-archive files.
 *
 * Each transcript keeps its logical path `<...>.jsonl` (exchanges.archive_path).
 * After `maintain` compacts a file only `<...>.jsonl.zst` remains. Readers resolve
 * the logical path here: a raw file wins when present (sync re-copied a newer
 * version), otherwise the compressed file is decoded transparently.
 */
import fs from 'fs';
import path from 'path';
import zlib from 'zlib';
import { pipeline, Readable } from 'stream';

export const ZST_SUFFIX = '.zst';

export function zstdAvailable(): boolean {
  return typeof zlib.createZstdCompress === 'function'
    && typeof zlib.createZstdDecompress === 'function'
    && typeof zlib.zstdDecompressSync === 'function';
}

export function logicalPath(p: string): string {
  return p.endsWith('.jsonl' + ZST_SUFFIX) ? p.slice(0, -ZST_SUFFIX.length) : p;
}

/** The file on disk for a logical path: the raw .jsonl, else its .jsonl.zst, else null. */
export function resolveArchiveFile(p: string): string | null {
  const logical = logicalPath(p);
  if (fs.existsSync(logical)) return logical;
  const compressed = logical + ZST_SUFFIX;
  if (fs.existsSync(compressed)) return compressed;
  return null;
}

export function archiveExists(p: string): boolean {
  return resolveArchiveFile(p) !== null;
}

export function archiveStat(p: string): fs.Stats | null {
  const file = resolveArchiveFile(p);
  return file ? fs.statSync(file) : null;
}

function notFound(p: string): NodeJS.ErrnoException {
  const err: NodeJS.ErrnoException = new Error(`ENOENT: archive file not found: ${logicalPath(p)}`);
  err.code = 'ENOENT';
  return err;
}

function assertZstd(file: string): void {
  if (!zstdAvailable()) {
    throw new Error(
      `Cannot read ${file}: Node ${process.version} has no built-in zstd (needs Node >= 22.15). ` +
      `Restore the original with: zstd -d "${file}"`
    );
  }
}

export function openArchiveStream(p: string): Readable {
  const file = resolveArchiveFile(p);
  if (!file) throw notFound(p);
  const raw = fs.createReadStream(file);
  if (!file.endsWith(ZST_SUFFIX)) return raw;
  assertZstd(file);
  const decoded = zlib.createZstdDecompress();
  pipeline(raw, decoded, () => { /* an error destroys `decoded`, so the reader sees it */ });
  return decoded;
}

export function readArchiveText(p: string): string {
  const file = resolveArchiveFile(p);
  if (!file) throw notFound(p);
  const buf = fs.readFileSync(file);
  if (!file.endsWith(ZST_SUFFIX)) return buf.toString('utf-8');
  assertZstd(file);
  return zlib.zstdDecompressSync(buf).toString('utf-8');
}

/** True when `dest` (raw or compacted) is missing or older than `src`. */
export function archiveNeedsCopy(src: string, dest: string): boolean {
  const existing = archiveStat(dest);
  return !existing || existing.mtimeMs < fs.statSync(src).mtimeMs;
}

/** Where an archive copy is kept when its replacement would be smaller: `<p>.superseded-<mtimeMs>`. */
export function supersededName(p: string, mtimeMs: number): string {
  return `${p}.superseded-${Math.trunc(mtimeMs)}`;
}

/**
 * Copy `src` into the archive at `dest` when needed. COPYFILE_FICLONE makes the copy a
 * free clone on APFS; on filesystems without copy-on-write Node falls back to a real
 * copy, so a file is never skipped. Atomic: temp file + rename. A raw archive copy larger
 * than `src` (the source was rewritten smaller) is kept as a superseded copy, never
 * overwritten (R5); the new copy is complete before the old one moves aside, so `dest`
 * is never left empty.
 */
export function copyIntoArchive(src: string, dest: string): boolean {
  if (!archiveNeedsCopy(src, dest)) return false;
  fs.mkdirSync(path.dirname(dest), { recursive: true });
  let keep: string | null = null;
  if (fs.existsSync(dest)) {
    const old = fs.statSync(dest);
    if (old.size > fs.statSync(src).size) {
      keep = supersededName(dest, old.mtimeMs);
      if (fs.existsSync(keep) || fs.existsSync(keep + ZST_SUFFIX)) {
        throw new Error(`refusing to overwrite ${keep}: archive copy ${dest} kept unchanged`);
      }
    }
  }
  const tmp = dest + '.tmp.' + process.pid;
  fs.copyFileSync(src, tmp, fs.constants.COPYFILE_FICLONE);
  if (keep) fs.renameSync(dest, keep);
  fs.renameSync(tmp, dest);
  return true;
}
