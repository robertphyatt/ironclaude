# Episodic Memory Lean — Final-Review Fixes Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Fix the four items the operator chose after the final review: a stale partial slim build never causes a refusal (M1); a damaged `.zst` fails fast and never blocks the backfill (item 2); backfill skips a never-slimmed index without writing (item 5); a larger archive copy is never overwritten by a smaller source (guard 3).

**Requirements:** `docs/plans/2026-10-08-episodic-memory-lean-review-fixes-requirements.md`

**Design:** `docs/plans/2026-10-08-episodic-memory-lean-review-fixes-design.md`

**Architecture:** Small, fail-safe edits inside the existing modules of `worker/mcp-servers/episodic-memory/src/` (`maintain.ts`, `parser.ts`, `codex-parser.ts`, `archive-io.ts`) plus one README paragraph. One naming helper, `supersededName`, in `archive-io.ts` is shared by sync and compaction.

**Tech Stack:** TypeScript, Node 24 (built-in zstd), better-sqlite3 + sqlite-vec, vitest.

Loop: full — touches index DB writes (slim, backfill) and the archive copy path where data can be lost.

---

## Execution invariants

- Shell state does not persist between steps: every command uses literal absolute paths.
- Bash cwd may be anywhere: use `git -C /Users/roberthyatt/Code/ironclaude` and absolute paths.
- zsh has `nomatch`: quote globs.
- `docs/` is gitignored: docs artifacts need `git add -f`.
- No `2>/dev/null` on evidence commands; record actual result lines.
- Tests run only in `mkdtemp` directories. Nothing touches `~/.config/superpowers`, `~/.claude` or `~/.codex`. Never write the operator's opt-out file.
- Code blocks below are applied verbatim; extract them by script, not by retyping. An anchor that does not match exactly once: stop and report.
- No commits. Stage only the task's allowed files.

`VT` below means:
`/Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory/node_modules/.bin/vitest run --root /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory`

Baseline (measured 2026-10-07, after the parent loop): `Test Files 18 passed (18)`, `Tests 126 passed (126)`.

---

## Task 1: Stale partial slim build never causes a refusal (R1)

**Files:**
- Modify: `worker/mcp-servers/episodic-memory/src/maintain.ts` (`slimIndex`, the guard block at lines 253-261)
- Test: `worker/mcp-servers/episodic-memory/src/maintain-slim.test.ts`

**Step 1: Write the tests (RED).** No import changes: `maintain-slim.test.ts` already imports `Database`, `sqliteVec`, `fs` and `path`. Insert these two tests immediately before the final `});` that closes `describe('slimIndex', …)`, that is, after the closing `  });` of the test `'purges machine rows in place when the index never had tool_calls (new install)'`:

```ts
  it('removes a stale partial build before measuring free disk, so it never causes a refusal', async () => {
    const { slimIndex, estimateSlimBytes } = await import('./maintain.js');
    const ro = new Database(dbPath, { readonly: true });
    sqliteVec.load(ro);
    const est = estimateSlimBytes(ro);
    ro.close();
    const partial = 2 * est + 4096;
    fs.writeFileSync(dbPath + '.new', Buffer.alloc(partial));
    // Free disk as the volume reports it: the partial build's bytes come back once it is removed.
    const free = () => (2 * est - 1) + (fs.existsSync(dbPath + '.new') ? 0 : partial);
    const r = await slimIndex({ dbPath, freeBytes: free });
    expect(r.action).toBe('done');
    expect(tables(dbPath)).not.toContain('tool_calls');
  });

  it('dry run leaves a stale partial build in place and counts its bytes as free', async () => {
    const { slimIndex, estimateSlimBytes } = await import('./maintain.js');
    const ro = new Database(dbPath, { readonly: true });
    sqliteVec.load(ro);
    const est = estimateSlimBytes(ro);
    ro.close();
    const partial = 2 * est + 4096;
    fs.writeFileSync(dbPath + '.new', Buffer.alloc(partial));
    const free = () => (2 * est - 1) + (fs.existsSync(dbPath + '.new') ? 0 : partial);
    const r = await slimIndex({ dbPath, freeBytes: free, dryRun: true });
    expect(r.action).toBe('would-run');
    expect(r.detail).toContain(`stale partial build ${partial} bytes would be removed`);
    expect(fs.statSync(dbPath + '.new').size).toBe(partial);
    expect(tables(dbPath)).toContain('tool_calls');
  });
```

**Step 2: Run, see them fail.**

Run: `VT src/maintain-slim.test.ts`

Expected: `Tests  2 failed | 9 passed (11)`. Both new tests get `refused` (the guard counts the partial as used space).

**Step 3: Implement (GREEN).** In `maintain.ts`, replace exactly this block:

```ts
    const estimate = estimateSlimBytes(db);
    const free = (opts.freeBytes ?? defaultFreeBytes)(path.dirname(dbPath));
    const reclaimable = Math.max(0, fs.statSync(dbPath).size - estimate);
    if (free < 2 * estimate) {
      return report('slim', 'refused', `free disk ${free} bytes is below 2 x estimated slim size ${estimate} bytes; reclaimable ${reclaimable} bytes`);
    }
    if (opts.dryRun) {
      return report('slim', 'would-run', `estimated slim size ${estimate} bytes; free ${free} bytes; reclaimable ${reclaimable} bytes`);
    }
```

with:

```ts
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
```

**Step 4: Run, see them pass.**

Run: `VT src/maintain-slim.test.ts`

Expected: exit 0; `Tests  11 passed (11)`.

**Step 5: Stage.**

Run: `git -C /Users/roberthyatt/Code/ironclaude add worker/mcp-servers/episodic-memory/src/maintain.ts worker/mcp-servers/episodic-memory/src/maintain-slim.test.ts`

Expected: no output, exit 0.

---

## Task 2: Parsers reject on a damaged `.zst`; verify never lists superseded copies (R2, R7)

**Files:**
- Modify: `worker/mcp-servers/episodic-memory/src/parser.ts:54-58` and `:245-246`
- Modify: `worker/mcp-servers/episodic-memory/src/codex-parser.ts:27-29` and `:96`
- Create test: `worker/mcp-servers/episodic-memory/src/archive-damaged.test.ts`

**Step 1: Write the tests.** Create `src/archive-damaged.test.ts`:

```ts
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
```

**Step 2: Run the new file and record the result.**

Run: `VT src/archive-damaged.test.ts`

Expected: the positive control and the verify test pass. The two parser tests were not measurable at planning time (Bash was read-only): record whether each passes (Node's readline already forwards the decode error) or fails (a timeout means the hang is real). Either way, continue to Step 3: the change makes the rejection explicit instead of depending on readline internals. If the positive control fails, stop and report: the fixture is not damaged.

**Step 3: Implement.** In `parser.ts`, replace exactly:

```ts
  const fileStream = openArchiveStream(filePath);
  const rl = readline.createInterface({
    input: fileStream,
    crlfDelay: Infinity
  });
```

with:

```ts
  const fileStream = openArchiveStream(filePath);
  const rl = readline.createInterface({
    input: fileStream,
    crlfDelay: Infinity
  });
  // A damaged .zst (or any read error) must reject, never hang or return a partial parse (R2).
  const failure: { error?: Error } = {};
  fileStream.once('error', (error: Error) => { failure.error = error; rl.close(); });
```

and replace exactly:

```ts
  // Finalize last exchange
  finalizeExchange();
```

with:

```ts
  if (failure.error) throw failure.error;

  // Finalize last exchange
  finalizeExchange();
```

In `codex-parser.ts`, replace exactly:

```ts
  const rl = readline.createInterface({
    input: openArchiveStream(filePath), crlfDelay: Infinity,
  });
```

with:

```ts
  const archiveStream = openArchiveStream(filePath);
  const rl = readline.createInterface({ input: archiveStream, crlfDelay: Infinity });
  // A damaged .zst (or any read error) must reject, never hang or return a partial parse (R2).
  const failure: { error?: Error } = {};
  archiveStream.once('error', (error: Error) => { failure.error = error; rl.close(); });
```

and replace exactly:

```ts
  finalize();
  // Codex provenance: session_meta.payload.originator ("Codex Desktop" is interactive).
```

with:

```ts
  if (failure.error) throw failure.error;
  finalize();
  // Codex provenance: session_meta.payload.originator ("Codex Desktop" is interactive).
```

**Step 4: Run, see them pass; the readers still work.**

Run: `VT src/archive-damaged.test.ts src/archive-readers.test.ts src/parser-voice.test.ts src/codex-parser.test.ts`

Expected: exit 0; `Tests  14 passed (14)` (4 + 4 + 5 + 1).

**Step 5: Stage.**

Run: `git -C /Users/roberthyatt/Code/ironclaude add worker/mcp-servers/episodic-memory/src/parser.ts worker/mcp-servers/episodic-memory/src/codex-parser.ts worker/mcp-servers/episodic-memory/src/archive-damaged.test.ts`

Expected: no output, exit 0.

---

## Task 3: Backfill skips unreadable sessions and never-slimmed indexes (R3, R4)

**Files:**
- Modify: `worker/mcp-servers/episodic-memory/src/maintain.ts` (`backfillVoice`)
- Test: `worker/mcp-servers/episodic-memory/src/maintain-run.test.ts`

**Step 1: Write the tests (RED).** In `maintain-run.test.ts`, add `import zlib from 'zlib';` after the line `import crypto from 'crypto';`. Then insert these three tests immediately after the closing `  });` of the test `'a later slim run still verifies and removes .bak after the backfill removed machine rows'` (still inside `describe('backfillVoice', …)`):

```ts
  it('treats a session whose transcript cannot be read as unreadable: keeps its rows and finishes the rest', async () => {
    const { askPath } = await seedPreLeanRows();
    const bad = path.join(path.dirname(askPath), '11111111-1111-4111-8111-111111111111.jsonl'); // sorts before the good session
    const body = zlib.zstdCompressSync(fs.readFileSync(ASK_FIXTURE), { params: { [zlib.constants.ZSTD_c_checksumFlag]: 1 } });
    body[body.length - 1] ^= 0xff; // the frame checksum no longer matches
    expect(() => zlib.zstdDecompressSync(body)).toThrow(); // positive control: the fixture really is damaged
    fs.writeFileSync(bad + '.zst', body);
    const { initDatabase, insertExchange, resetDatabaseCache } = await import('./db.js');
    const db = initDatabase();
    insertExchange(db, { id: 'bad-row', project: '-Users-alice-Code-app', timestamp: '2026-10-01T00:00:00.000Z', userMessage: 'kept as is', assistantMessage: 'a', archivePath: bad, lineStart: 1, lineEnd: 2 }, VEC);
    resetDatabaseCache();
    const { backfillVoice } = await import('./maintain.js');
    const r = await backfillVoice({});
    expect(r.action).toBe('done');
    expect(r.detail).toContain('unreadable archive files 1');
    expect(query(`SELECT user_message FROM exchanges WHERE id = 'bad-row'`)).toEqual([{ user_message: 'kept as is' }]);
    expect(query(`SELECT COUNT(*) AS c FROM exchanges WHERE user_message LIKE 'Q: %'`)).toEqual([{ c: 1 }]);
  });

  it('skips without writing when the index has not been slimmed (tool_calls still present)', async () => {
    makeLegacyDb(dbPath, archive);
    const { runMaintain } = await import('./maintain.js');
    const reports = await runMaintain({ only: 'backfill', archiveDir: archive });
    expect(reports.map(r => [r.phase, r.action])).toEqual([['backfill', 'skipped']]);
    expect(reports[0].detail).toContain('has not been slimmed yet');
    const tables = query(`SELECT name FROM sqlite_master WHERE type = 'table'`).map(r => r.name);
    expect(tables).toContain('tool_calls');
    expect(tables).not.toContain('meta');
    expect(query(`SELECT name FROM pragma_table_info('exchanges') WHERE name = 'session_class'`)).toEqual([]);
  });

  it('--only=backfill --dry-run on an index that has not been slimmed reports skipped, like the real run', async () => {
    makeLegacyDb(dbPath, archive);
    const { runMaintain } = await import('./maintain.js');
    const reports = await runMaintain({ only: 'backfill', dryRun: true, archiveDir: archive });
    expect(reports.map(r => [r.phase, r.action])).toEqual([['backfill', 'skipped']]);
    expect(reports[0].detail).toContain('has not been slimmed yet');
    const tables = query(`SELECT name FROM sqlite_master WHERE type = 'table'`).map(r => r.name);
    expect(tables).toContain('tool_calls');
    expect(tables).not.toContain('meta');
  });
```

**Step 2: Run, see them fail.**

Run: `VT src/maintain-run.test.ts`

Expected: `Tests  3 failed | 7 passed (10)`. The unreadable-session test fails because the parse error aborts the whole backfill (a rejection, or a timeout if the parser hangs); the real `--only=backfill` test because backfill migrates the legacy index and reports something other than `skipped`; the dry-run test because the dry-run branch reports `would-run`. The existing full dry-run test (`'a dry-run over a legacy index writes nothing'`) keeps passing before and after.

**Step 3: Implement (GREEN).** In `maintain.ts`, five edits.

(a) Replace exactly:

```ts
  failpoint?: 'after-copy' | 'after-first-rename' | 'corrupt-compressed';
}
```

with:

```ts
  failpoint?: 'after-copy' | 'after-first-rename' | 'corrupt-compressed';
  /** Set only by runMaintain: in a full run the slim phase precedes this backfill. */
  afterSlim?: boolean;
}
```

(a2) In `backfillVoice`, replace exactly:

```ts
  if (opts.dryRun) {
    // Read-only: a dry run must not run initDatabase's migrations on a legacy index.
    if (!fs.existsSync(dbPath)) return report('backfill', 'skipped', 'no index database yet');
    const ro = new Database(dbPath, { readonly: true, fileMustExist: true });
    try {
      if (getMeta(ro, 'voice_backfill_done') === '1') return report('backfill', 'skipped', 'voice backfill already done');
      const cursor = getMeta(ro, 'voice_backfill_cursor') ?? '';
      const n = (ro.prepare(`SELECT COUNT(DISTINCT archive_path) AS c FROM exchanges WHERE archive_path > ?`).get(cursor) as { c: number }).c;
      return report('backfill', 'would-run', `would backfill provenance and Q→A pairs for ${n} sessions`);
    } finally { ro.close(); }
  }
```

with:

```ts
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
```

(a3) In `runMaintain`, replace exactly:

```ts
    backfill: () => backfillVoice(o),
```

with:

```ts
    backfill: () => backfillVoice({ ...o, afterSlim: !opts.only }),
```

(b) Replace exactly:

```ts
  const counts = { sessions: 0, answers: 0, replaced: 0, machine: 0, missing: 0 };
```

with:

```ts
  const counts = { sessions: 0, answers: 0, replaced: 0, machine: 0, missing: 0, unreadable: 0 };
```

and replace exactly:

```ts
    const parsed = path.basename(p).startsWith('rollout-')
      ? await parseCodexConversation(p, project, p)
      : await parseConversation(p, project, p);
```

with:

```ts
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
```

(c) Replace exactly:

```ts
    `machine sessions removed from the index ${counts.machine}; missing archive files ${counts.missing}`);
```

with:

```ts
    `machine sessions removed from the index ${counts.machine}; missing archive files ${counts.missing}; ` +
    `unreadable archive files ${counts.unreadable}`);
```

**Step 4: Run, see them pass.**

Run: `VT src/maintain-run.test.ts src/maintain-slim.test.ts`

Expected: exit 0; `Tests  21 passed (21)` (10 + 11).

**Step 5: Stage.**

Run: `git -C /Users/roberthyatt/Code/ironclaude add worker/mcp-servers/episodic-memory/src/maintain.ts worker/mcp-servers/episodic-memory/src/maintain-run.test.ts`

Expected: no output, exit 0.

---

## Task 4: Sync never overwrites a larger archive copy (R5)

**Files:**
- Modify: `worker/mcp-servers/episodic-memory/src/archive-io.ts:85-97` (`copyIntoArchive`, new `supersededName`)
- Test: `worker/mcp-servers/episodic-memory/src/archive-io.test.ts`

**Step 1: Write the tests (RED).** In `archive-io.test.ts`, insert immediately before the final `});` that closes `describe('archive-io', …)`:

```ts
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
```

**Step 2: Run, see them fail.**

Run: `VT src/archive-io.test.ts`

Expected: `Tests  2 failed | 8 passed (10)`. The first and third new tests fail (today the larger copy is overwritten and nothing throws); the growth test passes before and after, guarding against the fix over-reaching.

**Step 3: Implement (GREEN).** In `archive-io.ts`, replace exactly:

```ts
/**
 * Copy `src` into the archive at `dest` when needed. COPYFILE_FICLONE makes the copy a
 * free clone on APFS; on filesystems without copy-on-write Node falls back to a real
 * copy, so a file is never skipped. Atomic: temp file + rename.
 */
export function copyIntoArchive(src: string, dest: string): boolean {
  if (!archiveNeedsCopy(src, dest)) return false;
  fs.mkdirSync(path.dirname(dest), { recursive: true });
  const tmp = dest + '.tmp.' + process.pid;
  fs.copyFileSync(src, tmp, fs.constants.COPYFILE_FICLONE);
  fs.renameSync(tmp, dest);
  return true;
}
```

with:

```ts
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
```

**Step 4: Run, see them pass; sync still works.**

Run: `VT src/archive-io.test.ts src/sync-lean.test.ts src/codex-sync.test.ts`

Expected: exit 0; `Tests  19 passed (19)` (10 + 4 + 5).

**Step 5: Stage.**

Run: `git -C /Users/roberthyatt/Code/ironclaude add worker/mcp-servers/episodic-memory/src/archive-io.ts worker/mcp-servers/episodic-memory/src/archive-io.test.ts`

Expected: no output, exit 0.

---

## Task 5: Compaction never overwrites a larger or undecodable `.zst`; superseded copies are compacted (R6, R7)

**Files:**
- Modify: `worker/mcp-servers/episodic-memory/src/maintain.ts` (import line 20, `listCompactable`, `compactOne`)
- Test: `worker/mcp-servers/episodic-memory/src/maintain-compact.test.ts`

**Step 1: Write the tests (RED).** In `maintain-compact.test.ts`, add `import zlib from 'zlib';` after the line `import crypto from 'crypto';`. Insert these four tests immediately before the closing `});` of `describe('compactArchive', …)` (that is, after the `'dry-run reports and changes nothing'` test):

```ts
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
```

**Step 2: Run, see them fail.**

Run: `VT src/maintain-compact.test.ts`

Expected: `Tests  3 failed | 14 passed (17)`. The larger-copy, undecodable-copy and superseded-raw tests fail (today the old `.zst` is overwritten and superseded raws are not listed); the normal-growth test passes before and after.

**Step 3: Implement (GREEN).** In `maintain.ts`, four edits.

(a) Replace exactly:

```ts
import { ZST_SUFFIX, zstdAvailable, archiveExists } from './archive-io.js';
```

with:

```ts
import { ZST_SUFFIX, zstdAvailable, archiveExists, supersededName } from './archive-io.js';
```

(b) Replace exactly:

```ts
const MANIFEST = 'manifest.jsonl';
const MANIFEST_ERRORS = 'manifest-errors.jsonl';

/** Every transcript .jsonl under the archive (nested subagents included), never root files or temp copies. */
```

with:

```ts
const MANIFEST = 'manifest.jsonl';
const MANIFEST_ERRORS = 'manifest-errors.jsonl';
const SUPERSEDED_RE = /\.jsonl\.superseded-\d+$/;

/** Every transcript .jsonl and superseded raw copy under the archive (nested subagents included), never root files or temp copies. */
```

(c) Replace exactly:

```ts
      if (!e.isFile() || !e.name.endsWith('.jsonl') || e.name.includes('.tmp.')) continue;
```

with:

```ts
      const transcript = e.name.endsWith('.jsonl') || SUPERSEDED_RE.test(e.name);
      if (!e.isFile() || !transcript || e.name.includes('.tmp.')) continue;
```

(d) Replace exactly:

```ts
    fs.rmSync(zst, { force: true });
  }

  const tmp = zst + '.tmp';
```

with:

```ts
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
```

**Step 4: Run, see them pass.**

Run: `VT src/maintain-compact.test.ts src/maintain-run.test.ts`

Expected: exit 0; `Tests  27 passed (27)` (17 + 10).

**Step 5: Stage.**

Run: `git -C /Users/roberthyatt/Code/ironclaude add worker/mcp-servers/episodic-memory/src/maintain.ts worker/mcp-servers/episodic-memory/src/maintain-compact.test.ts`

Expected: no output, exit 0.

---

## Task 6: README — superseded copies (R8)

No tests required: documentation change.

**Files:**
- Modify: `README.md` (the `## Episodic Memory` section only). `README.md` also carries a separate effort's unfinished, staged edits; Steps 3-5 stage only this effort's hunk and leave that work unstaged in the working tree.

**Step 1: Insert the paragraph.** Insert the following immediately before the line `### Backup mirror (optional)` (anchor must match exactly once), followed by one blank line:

```markdown
**Superseded copies.** If a transcript's source is rewritten smaller than the archived copy, the archive keeps the old copy beside the new one as `<session>.jsonl.superseded-<mtime>` instead of overwriting it; `maintain` later compresses it to `.zst` and records it in the manifest, so the backup mirror includes it. An old compressed copy that no longer decodes is kept the same way. Superseded copies are never indexed: index rows built from the old content open the current file in `read`. Restore one with `zstd -d <file>.zst`.
```

**Step 2: Verify placement.**

Run: `grep -n -e "^\*\*Superseded copies\.\*\*" -e "^### Compressed archive" -e "^### Backup mirror (optional)" /Users/roberthyatt/Code/ironclaude/README.md`

Expected: exactly 3 lines, in order: `### Compressed archive, and how to restore an original`, `**Superseded copies.**…`, `### Backup mirror (optional)`.

**Step 3: Write the episodic-memory-only patch.** `README.md` also carries a separate effort's unfinished edits (the Codex provider paragraph and `### Managed-worker capacity handoff`), currently staged. Git stages whole files, so a plain `git add README.md` would put that work into this commit. Split by hunk instead:

Run:
```bash
python3 - <<'PY'
import re, subprocess
R = '/Users/roberthyatt/Code/ironclaude'
full = subprocess.run(['git', '-C', R, 'diff', 'HEAD', '--', 'README.md'], check=True, capture_output=True, text=True).stdout
head, *hunks = re.split(r'(?m)^(?=@@ )', full)
handoff = [h for h in hunks if 'Managed-worker capacity handoff' in h or 'guarded automatic capacity handoff' in h]
keep = [h for h in hunks if h not in handoff]
assert len(hunks) == 2 and len(handoff) == 1 and len(keep) == 1, (len(hunks), len(handoff), len(keep))
assert '+## Episodic Memory' in keep[0] and '+**Superseded copies.**' in keep[0]
open('/tmp/ic-em-readme-episodic.patch', 'w').write(head + keep[0])
print('hunks', len(hunks), 'kept 1, handoff left unstaged 1')
PY
```

Expected: `hunks 2 kept 1, handoff left unstaged 1`. Any assertion error: stop and report.

**Step 4: Stage only the episodic-memory hunk.** This resets only `README.md` in the index to `HEAD`, then applies the episodic-memory hunk to the index. The working tree keeps both sets of edits, so the handoff work is not lost; it is simply left unstaged for its own commit.

Run: `git -C /Users/roberthyatt/Code/ironclaude restore --staged -- README.md && git -C /Users/roberthyatt/Code/ironclaude apply --cached /tmp/ic-em-readme-episodic.patch && echo STAGED`

Expected: `STAGED`.

**Step 5: Verify the split.**

Run: `git -C /Users/roberthyatt/Code/ironclaude diff --cached -- README.md | grep -c -e '^+## Episodic Memory$' -e '^+\*\*Superseded copies\.\*\*' -e 'Managed-worker capacity handoff'`

Expected: `2` (the episodic heading and the superseded paragraph; no handoff text in the index).

Run: `git -C /Users/roberthyatt/Code/ironclaude diff -- README.md | grep -c -e 'Managed-worker capacity handoff' -e 'Episodic Memory'`

Expected: `1` (only the handoff heading remains as an unstaged change; nothing of the episodic section).

---

## Task 7: Full verification

No new tests: verification-only task; results are recorded in `docs/reviews/2026-10-08-episodic-memory-lean-review-fixes-verification.md`.

**Files:**
- Create: `docs/reviews/2026-10-08-episodic-memory-lean-review-fixes-verification.md`

**Step 1: Full episodic-memory suite.**

Run: `VT`

Expected: exit 0; `Test Files  19 passed (19)`, `Tests  142 passed (142)` (126 baseline + 2 + 4 + 3 + 3 + 4). Record the actual lines.

**Step 2: Type-check.**

Run: `/Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory/node_modules/.bin/tsc --noEmit -p /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory/tsconfig.json`

Expected: no output, exit 0.

**Step 3: Confirm the operator's opt-out before building.** Any output other than `1`, or a missing file: stop, do not build, report. Never write that file.

Run: `grep -c '"autoRun":false' /Users/roberthyatt/.config/superpowers/episodic-memory.json`

Expected: `1`

**Step 4: Build (dist is gitignored, not staged).**

Run: `npm --prefix /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory run build`

Expected: exit 0; esbuild lines for `dist/mcp-server.js`, `dist/sync-cli.js`, `dist/maintain-cli.js`.

**Step 5: Smoke-test the bundled CLI on throwaway directories.**

Run: `env EPISODIC_MEMORY_CONFIG_DIR=/tmp/ic-em-fix-check/config TEST_DB_PATH=/tmp/ic-em-fix-check/db.sqlite TEST_ARCHIVE_DIR=/tmp/ic-em-fix-check/archive node /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory/dist/maintain-cli.js --dry-run`

Expected: exit 0; exactly 4 lines: `slim: skipped — no index database yet`, `backfill: skipped — no index database yet`, `compact: skipped — nothing to compact`, `mirror: skipped — no backup.mirrorDir configured`.

**Step 6: Confirm the staged set.**

Run: `git -C /Users/roberthyatt/Code/ironclaude diff --cached --name-only -- worker/mcp-servers/episodic-memory README.md`

Expected: 38 paths: the 37 recorded in `docs/reviews/2026-10-07-episodic-memory-lean-verification.md` plus `worker/mcp-servers/episodic-memory/src/archive-damaged.test.ts`; nothing under `dist/`. Compare by script, not by eye.

**Step 6b: Confirm the staged README holds no handoff text.**

Run: `git -C /Users/roberthyatt/Code/ironclaude diff --cached -- README.md | grep -c -e '^+## Episodic Memory$' -e '^+\*\*Superseded copies\.\*\*' -e 'Managed-worker capacity handoff'`

Expected: `2`.

**Step 7: Write the verification note** recording every command above with its actual result lines, the Task 2 Step 2 parser result (pass, or timeout), the staged-set comparison, and the README split result.

**Completion note for the orchestrator:** the end-of-plan union restage must exclude `README.md` (a whole-file `git add` would re-stage the handoff hunk); re-run Step 6b afterwards.

**Step 8: Stage the note.**

Run: `git -C /Users/roberthyatt/Code/ironclaude add -f docs/reviews/2026-10-08-episodic-memory-lean-review-fixes-verification.md`

Expected: no output, exit 0.
