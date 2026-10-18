/**
 * maintain.ts — idempotent upkeep for episodic memory. Callers hold the sync lock.
 *
 *   slim     one-time rebuild of a legacy index without tool_calls and machine rows
 *   backfill one-time Q→A + provenance backfill from the archive
 *   compact  .jsonl → .jsonl.zst once a file is no longer live (sha256-gated)
 *   mirror   optional copy of verified .zst files to a user-chosen directory
 *
 * Every phase records progress (meta rows, manifest.jsonl), so a finished phase is
 * a cheap no-op and an interrupted one resumes. Nothing is deleted before
 * verification passes. Never VACUUM the old file; never use `index-cli rebuild`.
 */
import Database from 'better-sqlite3';
import * as sqliteVec from 'sqlite-vec';
import fs from 'fs';
import path from 'path';
import crypto from 'crypto';
import zlib from 'zlib';
import { pipeline } from 'stream/promises';
import { ZST_SUFFIX, zstdAvailable, archiveExists, supersededName } from './archive-io.js';
import { applySchema, getMeta, setMeta, tableExists, resetDatabaseCache, initDatabase, insertExchange, deleteExchange, ensureProvenanceIndexes } from './db.js';
import { getArchiveDir, getDbPath } from './paths.js';
import { loadSettings, classifySession, EpisodicSettings } from './settings.js';
import { parseConversation } from './parser.js';
import { parseCodexConversation } from './codex-parser.js';

export type Phase = 'slim' | 'backfill' | 'compact' | 'mirror';
export const PHASES: Phase[] = ['slim', 'backfill', 'compact', 'mirror'];

export interface PhaseReport {
  phase: Phase;
  action: 'skipped' | 'would-run' | 'done' | 'refused' | 'error';
  detail: string;
}

export interface MaintainOptions {
  dryRun?: boolean;
  keepBackup?: boolean;
  invokedBy?: 'cli' | 'sync';
  dbPath?: string;
  archiveDir?: string;
  settings?: EpisodicSettings;
  now?: number;
  /** Test seam: free bytes on the volume holding `dir`. */
  freeBytes?: (dir: string) => number;
  /** Test seam: simulate a crash or corruption at a named point. */
  failpoint?: 'after-copy' | 'after-first-rename' | 'corrupt-compressed';
  /** Set only by runMaintain: in a full run the slim phase precedes this backfill. */
  afterSlim?: boolean;
}

const TOOL_CALL_OBJECTS = new Set(['tool_calls', 'sqlite_autoindex_tool_calls_1', 'idx_tool_name', 'idx_tool_exchange']);

export function defaultFreeBytes(dir: string): number {
  const s = fs.statfsSync(dir);
  return Number(s.bavail) * Number(s.bsize);
}

function openDb(file: string): Database.Database {
  const db = new Database(file);
  sqliteVec.load(db);
  return db;
}

function report(phase: Phase, action: PhaseReport['action'], detail: string): PhaseReport {
  return { phase, action, detail };
}

/** Bytes the slim index needs: every b-tree except tool_calls and its indexes (dbstat per object). */
export function estimateSlimBytes(db: Database.Database): number {
  const objects = (db.prepare(`SELECT name FROM sqlite_master WHERE type IN ('table', 'index') AND rootpage > 0`).all() as Array<{ name: string }>)
    .map(r => r.name)
    .filter(name => !TOOL_CALL_OBJECTS.has(name));
  const stmt = db.prepare(`SELECT COALESCE(SUM(pgsize), 0) AS bytes FROM dbstat WHERE name = ? AND aggregate = TRUE`);
  return objects.reduce((sum, name) => sum + (stmt.get(name) as { bytes: number }).bytes, 0);
}

function registerClassifier(db: Database.Database, settings: EpisodicSettings): void {
  db.function('ic_session_class', { deterministic: true }, (project: unknown, entrypoint: unknown) =>
    classifySession({ project: String(project), entrypoint: typeof entrypoint === 'string' ? entrypoint : undefined }, settings));
}

function hasColumn(db: Database.Database, table: string, column: string): boolean {
  return (db.prepare(`SELECT name FROM pragma_table_info(?)`).all(table) as Array<{ name: string }>).some(r => r.name === column);
}

/** Index without tool_calls (a new install): drop machine-session rows in place, once. */
function purgeMachineRows(db: Database.Database, settings: EpisodicSettings, opts: MaintainOptions): PhaseReport {
  if (getMeta(db, 'machine_purge_done') === '1') return report('slim', 'skipped', 'index is already slim');
  registerClassifier(db, settings);
  const ep = hasColumn(db, 'exchanges', 'entrypoint') ? 'entrypoint' : 'NULL';
  const ids = (db.prepare(`SELECT id FROM exchanges WHERE ic_session_class(project, ${ep}) IN ('machine', 'archive-only')`).all() as Array<{ id: string }>).map(r => r.id);
  if (opts.dryRun) return report('slim', 'would-run', `no tool_calls table; would remove ${ids.length} machine-session rows in place`);
  applySchema(db, { provenanceIndexes: true });
  const dv = db.prepare(`DELETE FROM vec_exchanges WHERE id = ?`);
  const de = db.prepare(`DELETE FROM exchanges WHERE id = ?`);
  db.transaction(() => { for (const id of ids) { dv.run(id); de.run(id); } })();
  setMeta(db, 'machine_purge_done', '1');
  return report('slim', 'done', `removed ${ids.length} machine-session rows in place`);
}

function verifyCurrent(db: Database.Database): boolean {
  const ok = db.pragma('quick_check', { simple: true }) === 'ok';
  const expected = getMeta(db, 'slim_rows');
  const rows = (db.prepare(`SELECT COUNT(*) AS c FROM exchanges`).get() as { c: number }).c;
  // The backfill legitimately deletes rows after the slim: machine/archive-only sessions
  // caught by first-message prefix, and superseded re-split rows. It records them in
  // meta.backfill_deleted, so net them out; any other shrinkage still fails verification.
  const deleted = Number(getMeta(db, 'backfill_deleted') ?? '0');
  return ok && expected !== null && rows + deleted >= Number(expected);
}

/**
 * Compare a fixed sample of KNN searches. For the first 3 rows, the old index's hits
 * restricted to kept rows must equal the new index's top hits over their common
 * length n: same distances, and every new id among the old kept hits (ties may
 * reorder). k = 200 on the old side so machine near-duplicates do not crowd out n.
 */
function sampleSearchMismatches(nw: Database.Database): string[] {
  const problems: string[] = [];
  const sample = nw.prepare(`SELECT id FROM main.exchanges ORDER BY rowid LIMIT 3`).all() as Array<{ id: string }>;
  const getVec = nw.prepare(`SELECT embedding FROM old.vec_exchanges WHERE id = ?`);
  const knnNew = nw.prepare(`SELECT id, distance FROM main.vec_exchanges WHERE embedding MATCH ? AND k = 5 ORDER BY distance`);
  const knnOld = nw.prepare(`SELECT id, distance FROM old.vec_exchanges WHERE embedding MATCH ? AND k = 200 ORDER BY distance`);
  const kept = nw.prepare(`SELECT 1 FROM main.exchanges WHERE id = ?`);
  for (const { id } of sample) {
    const row = getVec.get(id) as { embedding: Buffer } | undefined;
    if (!row) continue;
    const fresh = knnNew.all(row.embedding) as Array<{ id: string; distance: number }>;
    const legacyKept = (knnOld.all(row.embedding) as Array<{ id: string; distance: number }>).filter(r => kept.get(r.id));
    const n = Math.min(fresh.length, legacyKept.length);
    const d = (rs: Array<{ distance: number }>) => rs.slice(0, n).map(r => r.distance.toFixed(6)).join(',');
    const legacyIds = new Set(legacyKept.map(r => r.id));
    if (n === 0 || d(fresh) !== d(legacyKept) || !fresh.slice(0, n).every(r => legacyIds.has(r.id))) {
      problems.push(`sample search for ${id} differs: [${fresh.map(r => r.id)}] vs [${legacyKept.slice(0, n).map(r => r.id)}]`);
    }
  }
  return problems;
}

function buildAndSwap(dbPath: string, newPath: string, bakPath: string, settings: EpisodicSettings, opts: MaintainOptions): PhaseReport {
  const removeNew = () => { for (const f of [newPath, `${newPath}-wal`, `${newPath}-shm`]) fs.rmSync(f, { force: true }); };
  removeNew(); // a partial build (and its -wal/-shm) from an interrupted run
  const nw = openDb(newPath);
  let swapReady = false;
  try {
    nw.pragma('journal_mode = OFF');  // a fresh file: no rollback needed; keeps peak disk ~= final size
    nw.pragma('synchronous = OFF');
    applySchema(nw, { provenanceIndexes: true });
    registerClassifier(nw, settings);
    nw.prepare(`ATTACH DATABASE ? AS old`).run(dbPath);

    const oldCols = new Set((nw.pragma(`old.table_info(exchanges)`) as Array<{ name: string }>).map(r => r.name));
    const newCols = (nw.pragma(`table_info(exchanges)`) as Array<{ name: string }>).map(r => r.name);
    const cols = newCols.filter(c => oldCols.has(c) && c !== 'session_class').join(', ');
    const ep = oldCols.has('entrypoint') ? 'entrypoint' : 'NULL';
    const keep = `(SELECT ${cols}, ic_session_class(project, ${ep}) AS cls FROM old.exchanges) WHERE cls NOT IN ('machine', 'archive-only')`;

    nw.transaction(() => {
      nw.exec(`INSERT INTO main.exchanges (${cols}, session_class) SELECT ${cols}, cls FROM ${keep}`);
      nw.exec(`INSERT INTO main.vec_exchanges (id, embedding) SELECT v.id, v.embedding FROM old.vec_exchanges AS v JOIN main.exchanges AS e ON e.id = v.id`);
    })();

    const count = (sql: string) => (nw.prepare(sql).get() as { c: number }).c;
    const expectedRows = count(`SELECT COUNT(*) AS c FROM ${keep}`);
    const newRows = count(`SELECT COUNT(*) AS c FROM main.exchanges`);
    const expectedVec = count(`SELECT COUNT(*) AS c FROM old.vec_exchanges AS v JOIN main.exchanges AS e ON e.id = v.id`);
    const newVec = count(`SELECT COUNT(*) AS c FROM main.vec_exchanges`);
    const maxOld = (nw.prepare(`SELECT MAX(timestamp) AS t FROM ${keep}`).get() as { t: string | null }).t;
    const maxNew = (nw.prepare(`SELECT MAX(timestamp) AS t FROM main.exchanges`).get() as { t: string | null }).t;

    const problems: string[] = [];
    if (newRows !== expectedRows) problems.push(`rows ${newRows} != ${expectedRows}`);
    if (newVec !== expectedVec) problems.push(`vectors ${newVec} != ${expectedVec}`);
    if (maxNew !== maxOld) problems.push(`MAX(timestamp) ${maxNew} != ${maxOld}`);
    problems.push(...sampleSearchMismatches(nw));
    if (problems.length > 0) {
      return report('slim', 'error', `verification failed; the old index is untouched: ${problems.join('; ')}`);
    }

    setMeta(nw, 'slim_rows', String(newRows));
    setMeta(nw, 'slim_done', '1');
    setMeta(nw, 'legacy_origin', '1');
    setMeta(nw, 'schema_version', '2');
    setMeta(nw, 'slim_verified', '1');
    nw.exec(`DETACH DATABASE old`);
    nw.pragma('journal_mode = WAL');
    swapReady = true;
  } finally {
    nw.close();
    if (!swapReady) removeNew();
  }

  if (opts.failpoint === 'after-copy') throw new Error('failpoint after-copy');

  // Atomic swap. The old WAL was checkpointed (TRUNCATE) before the build; remove its
  // -wal/-shm before the new file takes the name so they can never be replayed into it.
  fs.renameSync(dbPath, bakPath);
  if (opts.failpoint === 'after-first-rename') throw new Error('failpoint after-first-rename');
  for (const suffix of ['-wal', '-shm']) fs.rmSync(dbPath + suffix, { force: true });
  fs.renameSync(newPath, dbPath);
  resetDatabaseCache();
  return report('slim', 'done', `slim index built and swapped in; old index kept as ${path.basename(bakPath)}`);
}

export async function slimIndex(opts: MaintainOptions = {}): Promise<PhaseReport> {
  const dbPath = opts.dbPath ?? getDbPath();
  const settings = opts.settings ?? loadSettings();
  const newPath = `${dbPath}.new`;
  const bakPath = `${dbPath}.bak`;

  // Crash between the two renames: finish the swap if the new file was verified, else roll back.
  if (!fs.existsSync(dbPath) && fs.existsSync(bakPath)) {
    if (opts.dryRun) return report('slim', 'would-run', 'would finish an interrupted swap');
    let verified = false;
    if (fs.existsSync(newPath)) {
      const probe = openDb(newPath);
      try { verified = getMeta(probe, 'slim_verified') === '1'; } finally { probe.close(); }
    }
    for (const suffix of ['-wal', '-shm']) fs.rmSync(dbPath + suffix, { force: true });
    fs.renameSync(verified ? newPath : bakPath, dbPath);
    resetDatabaseCache();
    if (verified) return report('slim', 'done', `resumed: finished the interrupted swap; old index kept as ${path.basename(bakPath)}`);
  }
  if (!fs.existsSync(dbPath)) return report('slim', 'skipped', 'no index database yet');

  const db = openDb(dbPath);
  try {
    if (getMeta(db, 'slim_done') === '1') {
      const notes: string[] = ['index is already slim'];
      if (tableExists(db, 'tool_calls')) {
        // An older bundle (a stale plugin cache) recreated the table; drop it again.
        if (opts.dryRun) notes.push('would drop a recreated tool_calls table');
        else {
          db.exec(`DROP INDEX IF EXISTS idx_tool_name; DROP INDEX IF EXISTS idx_tool_exchange; DROP TABLE IF EXISTS tool_calls;`);
          notes.push('dropped a recreated tool_calls table');
        }
      }
      if (fs.existsSync(bakPath)) {
        // A LATER run (automatic or manual) removes the .bak after re-verifying; the slim run
        // itself never reaches here. --keep-bak and dry runs keep it.
        const removable = !opts.keepBackup && !opts.dryRun;
        if (!removable) notes.push(`kept ${path.basename(bakPath)} (${opts.dryRun ? 'dry run' : '--keep-bak'})`);
        else if (verifyCurrent(db)) {
          fs.rmSync(bakPath, { force: true });
          setMeta(db, 'slim_bak_removed', new Date().toISOString());
          notes.push(`verified the index and removed ${path.basename(bakPath)}`);
        } else notes.push(`kept ${path.basename(bakPath)}: verification of the current index failed`);
      }
      return report('slim', 'skipped', notes.join('; '));
    }

    if (!tableExists(db, 'tool_calls')) return purgeMachineRows(db, settings, opts);

    // A partial build left by an interrupted run (R1) must never cause a refusal: remove it
    // before measuring free disk. A dry run changes nothing and counts its bytes as free instead.
    const partials = [newPath, `${newPath}-wal`, `${newPath}-shm`].filter(f => fs.existsSync(f));
    const partialBytes = partials.reduce((sum, f) => sum + fs.statSync(f).size, 0);
    if (!opts.dryRun) for (const f of partials) fs.rmSync(f, { force: true });
    const estimate = estimateSlimBytes(db);
    const free = (opts.freeBytes ?? defaultFreeBytes)(path.dirname(dbPath)) + (opts.dryRun ? partialBytes : 0);
    const reclaimable = Math.max(0, fs.statSync(dbPath).size - estimate);
    if (free < 2 * estimate) {
      return report('slim', 'refused', `free disk ${free} bytes is below 2 x estimated slim size ${estimate} bytes; reclaimable ${reclaimable} bytes`);
    }
    if (opts.dryRun) {
      const stale = partialBytes > 0 ? `; stale partial build ${partialBytes} bytes would be removed` : '';
      return report('slim', 'would-run', `estimated slim size ${estimate} bytes; free ${free} bytes; reclaimable ${reclaimable} bytes${stale}`);
    }
    const checkpoint = db.pragma('wal_checkpoint(TRUNCATE)') as Array<{ busy: number }>;
    if (checkpoint[0]?.busy) {
      return report('slim', 'refused', 'WAL checkpoint was busy (a reader is mid-transaction); retry on the next run');
    }
  } finally {
    db.close();
  }

  return buildAndSwap(dbPath, newPath, bakPath, settings, opts);
}

// ---------------------------------------------------------------------------
// compact: lossless .jsonl -> .jsonl.zst (L7)
// ---------------------------------------------------------------------------

export interface ManifestEntry {
  path: string;          // archive-relative logical path (.jsonl)
  sha256: string;        // of the original bytes
  rawBytes: number;
  zstBytes: number;
  mtimeMs: number;       // original mtime, also stamped on the .zst
  sessionClass: string;  // 'machine' / 'archive-only' tag files kept for the record but not indexed (D2, L16)
  compactedAt: string;
}

const MANIFEST = 'manifest.jsonl';
const MANIFEST_ERRORS = 'manifest-errors.jsonl';
const SUPERSEDED_RE = /\.jsonl\.superseded-\d+$/;

/** Every transcript .jsonl and superseded raw copy under the archive (nested subagents included), never root files or temp copies. */
function listCompactable(archiveDir: string): string[] {
  const out: string[] = [];
  const stack = [archiveDir];
  while (stack.length) {
    const dir = stack.pop()!;
    let entries: fs.Dirent[];
    try { entries = fs.readdirSync(dir, { withFileTypes: true }); } catch { continue; }
    for (const e of entries) {
      const full = path.join(dir, e.name);
      if (e.isDirectory()) { stack.push(full); continue; }
      const transcript = e.name.endsWith('.jsonl') || SUPERSEDED_RE.test(e.name);
      if (!e.isFile() || !transcript || e.name.includes('.tmp.')) continue;
      if (dir === archiveDir) continue; // manifests live at the root; transcripts never do
      out.push(full);
    }
  }
  return out.sort();
}

async function sha256Of(stream: NodeJS.ReadableStream): Promise<string> {
  const hash = crypto.createHash('sha256');
  for await (const chunk of stream) hash.update(chunk as Buffer);
  return hash.digest('hex');
}

function readManifest(archiveDir: string): Map<string, ManifestEntry> {
  const file = path.join(archiveDir, MANIFEST);
  const map = new Map<string, ManifestEntry>();
  if (!fs.existsSync(file)) return map;
  for (const line of fs.readFileSync(file, 'utf-8').split('\n')) {
    if (!line.trim()) continue;
    try { const e = JSON.parse(line) as ManifestEntry; map.set(e.path, e); } catch { /* a torn last line */ }
  }
  return map;
}

function appendLine(file: string, obj: object): void {
  fs.appendFileSync(file, JSON.stringify(obj) + '\n');
  const fd = fs.openSync(file, 'r+');
  try { fs.fsyncSync(fd); } finally { fs.closeSync(fd); }
}

async function compactOne(
  file: string, archiveDir: string, settings: EpisodicSettings,
  manifest: Map<string, ManifestEntry>, opts: MaintainOptions
): Promise<'compacted' | 'resumed' | 'error'> {
  const rel = path.relative(archiveDir, file);
  const zst = file + ZST_SUFFIX;
  const before = fs.statSync(file);
  const rawSha = await sha256Of(fs.createReadStream(file));

  const known = manifest.get(rel);
  if (known && known.sha256 === rawSha && fs.existsSync(zst)) {
    // Crash after the manifest line: finish the removal, but only after re-verifying the
    // existing .zst still decodes to these exact bytes. A .zst damaged between runs is
    // discarded and the file is recompressed below; the raw copy is never removed unverified.
    let intact = false;
    try {
      intact = (await sha256Of(fs.createReadStream(zst).pipe(zlib.createZstdDecompress()))) === rawSha;
    } catch { intact = false; }
    if (intact) {
      fs.rmSync(file);
      return 'resumed';
    }
    fs.rmSync(zst, { force: true });
  }

  if (fs.existsSync(zst)) {
    // A compacted copy is about to be replaced by a re-copied raw file (R6). Normal growth
    // replaces it; an old copy that is larger (the source was rewritten smaller) or that no
    // longer decodes is kept under a superseded name, never overwritten. Its manifest line
    // (sha256 '' and rawBytes 0 when it no longer decodes) keeps it in the backup mirror.
    const oldStat = fs.statSync(zst);
    let oldBytes = -1;
    let oldSha = '';
    try {
      const hash = crypto.createHash('sha256');
      let n = 0;
      for await (const chunk of fs.createReadStream(zst).pipe(zlib.createZstdDecompress())) {
        hash.update(chunk as Buffer);
        n += (chunk as Buffer).length;
      }
      oldBytes = n;
      oldSha = hash.digest('hex');
    } catch { oldBytes = -1; }
    if (oldBytes < 0 || oldBytes > before.size) {
      const keepRel = supersededName(rel, oldStat.mtimeMs);
      const keep = path.join(archiveDir, keepRel);
      if (fs.existsSync(keep) || fs.existsSync(keep + ZST_SUFFIX)) {
        appendLine(path.join(archiveDir, MANIFEST_ERRORS), {
          path: rel, error: `superseded copy ${keepRel} already exists; the old .zst was kept, not replaced`, at: new Date().toISOString(),
        });
        return 'error';
      }
      fs.renameSync(zst, keep + ZST_SUFFIX);
      const kept: ManifestEntry = {
        path: keepRel, sha256: oldSha, rawBytes: Math.max(oldBytes, 0), zstBytes: oldStat.size,
        mtimeMs: oldStat.mtimeMs, sessionClass: classifySession({ project: rel.split(path.sep)[0] }, settings),
        compactedAt: new Date().toISOString(),
      };
      appendLine(path.join(archiveDir, MANIFEST), kept);
      manifest.set(keepRel, kept);
    }
  }

  const tmp = zst + '.tmp';
  await pipeline(
    fs.createReadStream(file),
    zlib.createZstdCompress({ params: {
      [zlib.constants.ZSTD_c_compressionLevel]: settings.maintain.zstdLevel,
      [zlib.constants.ZSTD_c_checksumFlag]: 1,
    } }),
    fs.createWriteStream(tmp),
  );
  if (opts.failpoint === 'corrupt-compressed') {
    const buf = fs.readFileSync(tmp);
    buf[Math.floor(buf.length / 2)] ^= 0xff;
    fs.writeFileSync(tmp, buf);
  }

  let problem = '';
  try {
    const roundTrip = await sha256Of(fs.createReadStream(tmp).pipe(zlib.createZstdDecompress()));
    if (roundTrip !== rawSha) problem = `sha256 mismatch after round trip (${roundTrip} != ${rawSha})`;
  } catch (error) {
    problem = `decompress failed: ${error instanceof Error ? error.message : String(error)}`;
  }
  const after = fs.statSync(file);
  if (!problem && (after.size !== before.size || after.mtimeMs !== before.mtimeMs)) problem = 'file changed during compaction';
  if (problem) {
    fs.rmSync(tmp, { force: true });
    appendLine(path.join(archiveDir, MANIFEST_ERRORS), { path: rel, error: problem, at: new Date().toISOString() });
    return 'error';
  }

  const fd = fs.openSync(tmp, 'r+');
  try { fs.fsyncSync(fd); } finally { fs.closeSync(fd); }
  fs.renameSync(tmp, zst);
  fs.utimesSync(zst, before.atime, before.mtime);
  const entry: ManifestEntry = {
    path: rel, sha256: rawSha, rawBytes: before.size, zstBytes: fs.statSync(zst).size,
    mtimeMs: before.mtimeMs, sessionClass: classifySession({ project: rel.split(path.sep)[0] }, settings),
    compactedAt: new Date().toISOString(),
  };
  appendLine(path.join(archiveDir, MANIFEST), entry);
  manifest.set(rel, entry);
  fs.rmSync(file); // only after compress, stream-decompress, sha256 match and the manifest line
  return 'compacted';
}

export async function compactArchive(opts: MaintainOptions = {}): Promise<PhaseReport> {
  if (!zstdAvailable()) return report('compact', 'skipped', `Node ${process.version} has no built-in zstd; archive left uncompressed`);
  const archiveDir = opts.archiveDir ?? getArchiveDir();
  const settings = opts.settings ?? loadSettings();
  const now = opts.now ?? Date.now();
  const liveMs = settings.maintain.liveWindowHours * 3_600_000;
  const candidates = listCompactable(archiveDir).filter(f => now - fs.statSync(f).mtimeMs >= liveMs);
  if (candidates.length === 0) return report('compact', 'skipped', 'nothing to compact');
  if (opts.dryRun) {
    const bytes = candidates.reduce((sum, f) => sum + fs.statSync(f).size, 0);
    return report('compact', 'would-run', `would compact ${candidates.length} files (${bytes} raw bytes)`);
  }
  const manifest = readManifest(archiveDir);
  const counts = { compacted: 0, resumed: 0, error: 0 };
  for (const file of candidates) counts[await compactOne(file, archiveDir, settings, manifest, opts)]++; // one at a time
  return report('compact', 'done', `compacted ${counts.compacted}, resumed ${counts.resumed}, errors ${counts.error} (see ${MANIFEST_ERRORS})`);
}

// ---------------------------------------------------------------------------
// mirror: optional, fail-soft, checksummed (L11)
// ---------------------------------------------------------------------------

export async function mirrorBackup(opts: MaintainOptions = {}): Promise<PhaseReport> {
  const settings = opts.settings ?? loadSettings();
  const target = settings.backup.mirrorDir;
  if (!target) return report('mirror', 'skipped', 'no backup.mirrorDir configured');
  const archiveDir = opts.archiveDir ?? getArchiveDir();
  const entries = [...readManifest(archiveDir).values()]
    .filter(e => fs.existsSync(path.join(archiveDir, e.path + ZST_SUFFIX)));
  const pending = entries.filter(e => {
    const src = path.join(archiveDir, e.path + ZST_SUFFIX);
    const dest = path.join(target, e.path + ZST_SUFFIX);
    if (!fs.existsSync(dest)) return true;
    const a = fs.statSync(src);
    const b = fs.statSync(dest);
    return a.size !== b.size || a.mtimeMs !== b.mtimeMs;
  });
  if (opts.dryRun) return report('mirror', 'would-run', `would mirror ${pending.length} files to ${target}`);
  try {
    fs.mkdirSync(target, { recursive: true });
    fs.accessSync(target, fs.constants.W_OK);
  } catch (error) {
    const msg = `backup mirror unavailable (${target}): ${error instanceof Error ? error.message : String(error)}; continuing without it`;
    console.warn(`[episodic-memory] ${msg}`);
    return report('mirror', 'skipped', msg);
  }
  let mirrored = 0;
  let failed = 0;
  for (const e of pending) {
    const src = path.join(archiveDir, e.path + ZST_SUFFIX);
    const dest = path.join(target, e.path + ZST_SUFFIX);
    const tmp = dest + '.tmp';
    try {
      fs.mkdirSync(path.dirname(dest), { recursive: true });
      fs.copyFileSync(src, tmp);
      const [a, b] = await Promise.all([sha256Of(fs.createReadStream(src)), sha256Of(fs.createReadStream(tmp))]);
      if (a !== b) throw new Error(`checksum mismatch (${b} != ${a})`);
      fs.renameSync(tmp, dest);
      const st = fs.statSync(src);
      fs.utimesSync(dest, st.atime, st.mtime);
      mirrored++;
    } catch (error) {
      fs.rmSync(tmp, { force: true });
      failed++;
      console.warn(`[episodic-memory] mirror of ${e.path} failed: ${error instanceof Error ? error.message : String(error)}`);
    }
  }
  return report('mirror', 'done', `mirrored ${mirrored}, failed ${failed}, already current ${entries.length - pending.length}`);
}

// ---------------------------------------------------------------------------
// backfill: one-time Q→A pairs + provenance from the archive (L12, L9)
// ---------------------------------------------------------------------------

/** Record rows the backfill removed, so a later .bak re-verification can net them out. */
function noteBackfillDeletions(db: Database.Database, n: number): void {
  if (n === 0) return;
  const prior = Number(getMeta(db, 'backfill_deleted') ?? '0');
  setMeta(db, 'backfill_deleted', String(prior + n));
}

export async function backfillVoice(opts: MaintainOptions = {}): Promise<PhaseReport> {
  const settings = opts.settings ?? loadSettings();
  const dbPath = opts.dbPath ?? getDbPath();

  if (fs.existsSync(dbPath)) {
    // Read-only first, dry and real runs alike: never migrate or write a legacy index from
    // here (R4). Only a full `maintain --dry-run` (afterSlim, set by runMaintain) reports the
    // backfill that will run once the slim has rebuilt the index.
    const ro = new Database(dbPath, { readonly: true, fileMustExist: true });
    try {
      const unslimmed = tableExists(ro, 'tool_calls');
      if (unslimmed && !(opts.dryRun && opts.afterSlim)) {
        return report('backfill', 'skipped', 'the index has not been slimmed yet; run maintain (slim) first');
      }
      if (opts.dryRun) {
        if (getMeta(ro, 'voice_backfill_done') === '1') return report('backfill', 'skipped', 'voice backfill already done');
        const cursor = getMeta(ro, 'voice_backfill_cursor') ?? '';
        const n = (ro.prepare(`SELECT COUNT(DISTINCT archive_path) AS c FROM exchanges WHERE archive_path > ?`).get(cursor) as { c: number }).c;
        const after = unslimmed ? '; runs after the slim rebuilds the index' : '';
        return report('backfill', 'would-run', `would backfill provenance and Q→A pairs for ${n} sessions${after}`);
      }
    } finally { ro.close(); }
  } else if (opts.dryRun) {
    return report('backfill', 'skipped', 'no index database yet');
  }

  const db = initDatabase();
  if (getMeta(db, 'voice_backfill_done') === '1') return report('backfill', 'skipped', 'voice backfill already done');
  ensureProvenanceIndexes(db); // built here, under the sync lock, never at MCP startup on an existing index
  const cursor = getMeta(db, 'voice_backfill_cursor') ?? '';
  const sessions = db.prepare(
    `SELECT archive_path AS p, MIN(project) AS project FROM exchanges WHERE archive_path > ? GROUP BY archive_path ORDER BY archive_path`
  ).all(cursor) as Array<{ p: string; project: string }>;

  const { initEmbeddings, generateExchangeEmbedding } = await import('./embeddings.js');
  let embeddingsReady = false;
  const idsFor = db.prepare(`SELECT id FROM exchanges WHERE archive_path = ?`);
  const stamp = db.prepare(`UPDATE exchanges SET entrypoint = ?, session_class = ? WHERE archive_path = ?`);
  const counts = { sessions: 0, answers: 0, replaced: 0, machine: 0, missing: 0, unreadable: 0 };

  for (const { p, project } of sessions) {
    if (!archiveExists(p)) {
      counts.missing++;
      setMeta(db, 'voice_backfill_cursor', p);
      continue;
    }
    let parsed: Awaited<ReturnType<typeof parseConversation>>;
    try {
      parsed = path.basename(p).startsWith('rollout-')
        ? await parseCodexConversation(p, project, p)
        : await parseConversation(p, project, p);
    } catch {
      // Unreadable (for example a damaged .zst): keep this session's rows exactly as they are
      // and move on, so one bad file never blocks the rest of the backfill (R3).
      counts.unreadable++;
      setMeta(db, 'voice_backfill_cursor', p);
      continue;
    }
    const entrypoint = parsed.find(e => e.entrypoint)?.entrypoint ?? null;
    const sessionClass = classifySession({ project, entrypoint, firstUserText: parsed[0]?.userMessage }, settings);
    const have = (idsFor.all(p) as Array<{ id: string }>).map(r => r.id);

    if (sessionClass === 'machine' || sessionClass === 'archive-only') {
      // Machine and archive-only sessions leave the index; their archive file is kept (and compacted).
      db.transaction(() => {
        for (const id of have) deleteExchange(db, id);
        noteBackfillDeletions(db, have.length);
      })();
      counts.machine++;
      setMeta(db, 'voice_backfill_cursor', p);
      continue;
    }

    stamp.run(entrypoint, sessionClass, p);

    if (parsed.some(e => e.isAnswer)) {
      // Answers split an old exchange in two, so its id changes. Insert the ids the
      // new parse produces and drop the superseded INDEX rows (L16): same transcript
      // text, re-segmented. Backfill only reads the archive; it never modifies it.
      const fresh = new Set(parsed.map(e => e.id));
      const existing = new Set(have);
      const toInsert = parsed.filter(e => !existing.has(e.id));
      const stale = have.filter(id => !fresh.has(id));
      if (!embeddingsReady) { await initEmbeddings(); embeddingsReady = true; }
      const prepared: Array<{ e: typeof parsed[number]; vec: number[] }> = [];
      for (const e of toInsert) {
        e.sessionClass = sessionClass;
        const toolNames = e.toolCalls?.map(tc => tc.toolName);
        prepared.push({ e, vec: await generateExchangeEmbedding(e.userMessage, e.assistantMessage, toolNames) });
      }
      db.transaction(() => {
        for (const { e, vec } of prepared) insertExchange(db, e, vec);
        for (const id of stale) deleteExchange(db, id);
        noteBackfillDeletions(db, stale.length);
      })();
      counts.answers += parsed.filter(e => e.isAnswer).length;
      counts.replaced += stale.length;
    }
    counts.sessions++;
    setMeta(db, 'voice_backfill_cursor', p);
  }

  setMeta(db, 'voice_backfill_done', '1');
  return report('backfill', 'done',
    `sessions ${counts.sessions}; Q→A exchanges ${counts.answers}; superseded rows replaced ${counts.replaced}; ` +
    `machine sessions removed from the index ${counts.machine}; missing archive files ${counts.missing}; ` +
    `unreadable archive files ${counts.unreadable}`);
}

// ---------------------------------------------------------------------------
// runner + automatic-run gate (L5, L15)
// ---------------------------------------------------------------------------

export async function runMaintain(opts: MaintainOptions & { only?: Phase } = {}): Promise<PhaseReport[]> {
  const settings = opts.settings ?? loadSettings();
  const o: MaintainOptions = { ...opts, settings };
  const run: Record<Phase, () => Promise<PhaseReport>> = {
    slim: () => slimIndex(o),
    backfill: () => backfillVoice({ ...o, afterSlim: !opts.only }),
    compact: () => compactArchive(o),
    mirror: () => mirrorBackup(o),
  };
  const reports: PhaseReport[] = [];
  let slimBlocked = false;
  for (const phase of PHASES) {
    if (opts.only && opts.only !== phase) continue;
    if (phase === 'backfill' && slimBlocked) {
      // Do not write into a legacy index the slim could not replace; compaction may
      // still free space so the slim fits next time.
      reports.push(report('backfill', 'skipped', 'the slim did not complete this run'));
      continue;
    }
    let r: PhaseReport;
    try { r = await run[phase](); }
    catch (error) { r = report(phase, 'error', error instanceof Error ? error.message : String(error)); }
    reports.push(r);
    if (phase === 'slim' && (r.action === 'refused' || r.action === 'error')) slimBlocked = true;
    else if (r.action === 'error') break; // stop; earlier phases left the stores consistent
  }
  return reports;
}

/**
 * maintain.autoRun (L16): true (default, every install including legacy-bloated ones) ->
 * the sync worker runs every phase; the phases' own guards (2x disk, lock, verification)
 * still decide what actually happens. false -> never (the operator's opt-out).
 */
export function autoRunAllowed(settings: EpisodicSettings): { allowed: boolean; reason: string } {
  return settings.maintain.autoRun
    ? { allowed: true, reason: 'maintain.autoRun is true' }
    : { allowed: false, reason: 'maintain.autoRun is false' };
}
