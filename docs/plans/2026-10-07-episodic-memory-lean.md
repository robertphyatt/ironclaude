# Episodic Memory: Lean and Useful — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Stop the index and archive bloat at its source, capture the operator's voice and session provenance for any operator, and ship an idempotent, guarded `maintain` routine (automatic from the sync worker, and a CLI) that reclaims the existing space losslessly.

**Requirements:** `docs/plans/2026-10-07-episodic-memory-lean-requirements.md` (L1–L16)

**Design:** `docs/plans/2026-10-07-episodic-memory-lean-design.md`

**Architecture:**
- One archive helper (`archive-io.ts`) resolves every logical `.jsonl` path to the raw file or its `.jsonl.zst`, and copies into the archive with `COPYFILE_FICLONE`. Every reader goes through it.
- `db.ts` stops creating and writing `tool_calls`, adds `entrypoint` and `session_class` columns (additive migration), a `meta` table, and an `archive_path` index.
- `settings.ts` loads `episodic-memory.json` and classifies sessions as `operator | brain | worker | machine | archive-only | unknown`, from the project directory, the transcript entrypoint and the first user message. Nothing names a person.
  - `machine` sessions are never archived or indexed.
  - `archive-only` sessions (the work-chunk pipeline, L16) are archived and compacted but never indexed.
  - `maintain.autoRun` defaults to on for every install (L16).
- Parsers turn AskUserQuestion answers into "Q: … / A: …" user messages and record the entrypoint (Claude `entrypoint`, Codex `session_meta.payload.originator`).
- Sync skips machine sessions, archives subagent transcripts without indexing them, and stamps provenance.
- `maintain.ts` runs four idempotent phases: `slim` (guarded ATTACH-copy rebuild plus atomic swap), `backfill` (Q→A pairs and provenance from the archive), `compact` (sha256-gated zstd), `mirror` (optional, fail-soft). `sync-lock.ts` gives the sync worker and the CLI one Node-side lock and gives the sync worker a runtime budget.

**Tech Stack:** TypeScript (ESM), better-sqlite3 12.4.1, sqlite-vec 0.1.7-alpha.2, Node built-in zlib zstd, vitest 3.2.4, esbuild 0.25; Bash hook suites; Commander pytest.

**Loop classification:** full loop. It changes a DB schema, migrates data, takes a lock, deletes files behind verification, and ships to all users.

---

## Execution prerequisite (operator go given, before Task 1 is claimed)

The orchestrator does this once, at the start of execution. It is not a task. It opts the operator's box out of the automatic `maintain` run (L16), so no build or cache refresh can start a real slim or compaction there before the manual dry run.
```bash
printf '%s\n' '{"maintain":{"autoRun":false}}' > /Users/roberthyatt/.config/superpowers/episodic-memory.json
```
```bash
cat /Users/roberthyatt/.config/superpowers/episodic-memory.json
```
Expected output, exactly:
```
{"maintain":{"autoRun":false}}
```

## Grounded facts this plan relies on

All of these were read from current source or measured read-only on 2026-10-07.

**Source**
- `worker/mcp-servers/episodic-memory/src/db.ts`: `tool_calls` CREATE at 88-100, its indexes at 129-134, its INSERT at 193-212. No other file in `src/` names `tool_calls`. `initDatabase` is 41-138 and `migrateSchema` is 8-37.
- Archive readers:
  - `mcp-server.ts:272` (`existsSync`), `:283` (`realpathSync`) and `:295` (`readFileSync`);
  - `show-cli.ts:49`;
  - `search.ts:172` (`createReadStream`) and `:191` (`statSync`);
  - `repair-cli.ts:61` (`existsSync`) and `:66` (`readFileSync`);
  - `parser.ts:33`;
  - `codex-parser.ts:28`;
  - `indexer.ts:20`, plus `existsSync`-then-copy at `:115-116`, `:223-225` and `:316-318`;
  - `verify.ts:50` (lists `.jsonl` only) and `:73` (`statSync`).
- **`verify.ts:98-108` is a data-loss path once files are compressed.** It marks every row whose `archive_path` is not in the `.jsonl` listing as orphaned, and `repairIndex` (`:126-129`) deletes those rows. It must learn `.jsonl.zst`.
- `sync.ts:36-57` and `codex-sync.ts:20-28` each copy with `copyIfNewer`, which compares only the raw destination's mtime. Once a file is compacted, the raw destination is gone, so every compacted file would be re-copied and fully re-indexed on the next sync.
- `sync.ts:106` lists `.jsonl` files directly under a project, so `<proj>/<session>/subagents/*.jsonl` is never archived. This layout was confirmed on disk under `~/.claude/projects/-Users-roberthyatt-Code-ironclaude/7c7f63a0-…/subagents/`.
- `parser.ts:152-155` skips a user record whose content is only a `tool_result`, so AskUserQuestion answers are dropped today. A real record has the shape `"toolUseResult":{"questions":[…],"answers":{"<question text>":"<answer text>"}}`.
- `paths.ts:30-47`: `getSuperpowersDir()` honours `EPISODIC_MEMORY_CONFIG_DIR`, then `PERSONAL_SUPERPOWERS_DIR`, then `XDG_CONFIG_HOME`. `getArchiveDir()` honours `TEST_ARCHIVE_DIR` (`:52-59`). `getDbPath()` honours `EPISODIC_MEMORY_DB_PATH` and `TEST_DB_PATH` (`:71-78`). `getExcludedProjects()` reads `exclude.txt` from the index directory (`:83-106`) and is kept.
- `package.json:15`: the `bundle` script esbuilds only `mcp-server` and `sync-cli`. `cli/mcp-server-wrapper.js:55-112` auto-builds only those two. `cli/episodic-memory.js:70-105` dispatches the subcommands.
- `worker/hooks/hooks.json:14-21`: SessionStart runs `episodic-memory-sync.sh --background`.

**The singleton guard (L4)**
- In `worker/hooks/episodic-memory-sync.sh`, the `mkdir` lock (`:31`) and the PID file (`:37`) are released by the EXIT trap (`:40-44`) as soon as `sync-cli.js --background` forks its detached child and exits (`sync-cli.ts:45-60`). The comment at `:51-53` says so.
- The detached child holds no lock at all. The only protection is the `pgrep -f episodic-memory/dist/sync-cli.js` check at `:54-58`, and it has no age bound, so a hung sync blocks every later sync indefinitely.
- A manual CLI run does not go through this hook, so `maintain` would race a sync.
- **Shown defects fixed here:** no lock covers the detached child or a manual run, and no runtime bound exists. The 2026-10-02 incident recorded 9 concurrent `sync-cli.js` processes running for 3–46 minutes (`docs/plans/2026-10-02-worktree-allocate-findings.md:69-77`). They were busy (70–250% CPU), not provably hung, so "never exits" is not claimed as a defect.

**Real-data classification markers** (read-only grep of `~/.claude/projects`, `~/.codex/sessions` and the archive)
- Claude `entrypoint` values seen: `cli`, `sdk-py`, `sdk-cli`.
  - `sdk-py` sessions in operator project directories begin "Review this change for security vulnerabilities." They are automated security reviews.
  - `sdk-cli` sessions begin "Output only your exact model id string and nothing else." They are probes.
  - Commander workers in `<repo>--ironclaude-worktrees-<uuid>` directories are `sdk-py`.
  - Brain sessions (`-Users-roberthyatt--ironclaude-brain`, CLI 2.1.59) carry no `entrypoint` field.
- **Full census.** A read-only `grep -rhoE -m1` over all of `~/.claude/projects` for any `entrypoint` other than `cli`, `sdk-py` or `sdk-cli` returned nothing. The positive control on a known `sdk-py` directory did match.
- Codex `session_meta.payload.originator` values seen, censused across all of `~/.codex/sessions` and `~/.codex/archived_sessions`:
  - `Codex Desktop`, interactive;
  - `codex-tui`, the interactive Codex CLI;
  - `codex_exec`;
  - `ironclaude`, a Codex Brain with cwd `~/.ironclaude/brain`;
  - `ic-ab` and `ic-b1`, whose cwd is under `/private/tmp/claude-502/…/scratchpad/`, so they are machine by path.

  The repo fixture `src/__fixtures__/codex-rollout-sample.jsonl` line 1 has `"originator":"Codex Desktop"`.
- **The classifier names every known value.** Operator values are `cli`, `Codex Desktop` and `codex-tui`. Automation values are `sdk-*`, `codex_exec` and `ironclaude`. Any other entrypoint is `unknown`, never `worker`, so an unseen interactive surface is not filtered out of `session_class: operator` as automation.
- Machine directory names seen:
  - `-Users-roberthyatt--ironclaude-grader` and `--tron-grader`;
  - `-private-var-folders-…-T-claude-judge--<rand>` (many);
  - `-private-tmp`, whose sessions begin with the summarizer prompt, and `-private-tmp-claude-502-…-scratchpad-…`;
  - `-private-var-folders-…-T-ironclaude-pm-off-<rand>`, `-…-T-tmp-<rand>` and `-var-folders-…-T-tmp-<rand>`;
  - Commander's `brain_cwd` default is `~/.ironclaude/brain` (`commander/src/ironclaude/config.py:48`).
- No validator transcript directory exists on this machine. IronClaude's hook validators call Ollama or OpenAI, not Claude sessions. The automated security reviews above are the validator-like sessions that do exist.

**Runtime and tooling**
- `node` on PATH is `/opt/homebrew/bin/node` → `Cellar/node@24/24.17.0`. `@types/node` 24.10.1 declares `createZstdCompress`, `createZstdDecompress`, `zstdDecompressSync`, `ZSTD_c_compressionLevel` and `ZSTD_c_checksumFlag`. **Not executed at planning:** the planning stage blocks `node -e`, so Task 1's positive-control test is the runtime proof. The `zstd` CLI is at `/opt/homebrew/opt/zstd`.
- better-sqlite3 is compiled with `SQLITE_ENABLE_DBSTAT_VTAB` (`node_modules/better-sqlite3/deps/defines.gypi:19`).
- The Xenova model cache exists (`node_modules/@xenova/transformers/.cache/Xenova`). `node_modules/.bin/tsx` exists.
- `worker/mcp-servers/*/dist/` is gitignored (`.gitignore:29`), and no file under `dist/` is tracked (`git ls-files`).
- `src/__tests__/parent-death-exit.test.ts` spawns `dist/mcp-server.js`, so the suite needs a built `dist/`.
- The MCP launch log (`~/.claude/ironclaude-mcp-episodic-memory.log`):
  - the latest Claude launch is from `~/.claude/plugins/cache/ironclaude/ironclaude/1.1.16/` (line 47190);
  - the last launch from the repo `worker/` directory is line 47114 (2026-10-04);
  - Codex launches come from `~/.codex/plugins/cache/ironclaude/ironclaude/1.1.16+codex.20261005094121/`.

  So Claude and Codex currently load episodic memory from the plugin caches, and new code reaches the operator's box when those caches are refreshed (deploy = cache refresh). It can also arrive through a session launched from the repo, as happened up to 2026-10-04.
- The archive holds 2,113 `-Users-roberthyatt-Code-roleplaying-agents-knowledge-base-programmatically-control-claude-work-chunk-…` directories. They are automated pipeline sessions.
- On the real data (read-only `ls`), `~/.config/superpowers/conversation-index/` holds only `db.sqlite`, `-wal` and `-shm`. There is no `episodic-memory.json` and no `exclude.txt`.

**Test baseline: not measured.** The planning stage blocks `node`, `npm` and `vitest`. A static count finds 19 `it(` cases in 6 files: `codex-parser` 1, `codex-sync` 5, `codex-paths` 2, `summarizer` 6, `embedding-backend` 3, `parent-death-exit` 2. Task 13 records the real result line.

## The automatic-run switch (L15, L16)

The switch is `maintain.autoRun`, a boolean in `<getSuperpowersDir()>/episodic-memory.json`. It ships as `true` for every install, including installs whose index is already bloated (L16).

| `autoRun` | What the sync worker does |
|---|---|
| `true` (default; also used when the file is absent) | runs all phases. The guards still apply: free disk must be at least 2× the slim size, the lock must be held, and verification must pass before any swap or deletion. |
| `false` | nothing |
| file present but invalid JSON | treated as `false` (fail safe) |

- **The operator's box.** It is opted out by writing `{"maintain":{"autoRun":false}}` **before** deploying, that is, before the cache refresh. This is the first step of the manual section after the loop. It is not a plan task, because it writes under `~/.config`. Nothing in this loop writes to `~/.config/superpowers`.
- **`db.sqlite.bak`.** The run that performs the slim never deletes it. A **later** run deletes it, whether automatic or manual, but only after re-verifying the slim index (quick check, plus a row count at least the recorded one). A manual run with `--keep-bak` keeps it. A dry run never deletes it.

## Execution invariants

- **No persistent shell state.** Each step is its own invocation. Use the literal absolute paths shown; never rely on a variable from an earlier step.
- **Bash cwd may be `commander/`.** Every command uses absolute paths, and staging uses `git -C /Users/roberthyatt/Code/ironclaude add <explicit paths>`. Never use `git add -A` or `.`: the index already holds other efforts' staged files.
- **zsh `nomatch`.** Quote every glob. The only unquoted globs are the three hook-suite globs in Task 13, each confirmed to match by `find` at planning.
- **No foreground `sleep`.** No step waits.
- **`docs/` is gitignored.** The plan files need `git add -f`. No other `docs/` file is written.
- **Empty versus failed.** Evidence commands keep stderr: no `2>/dev/null` and no `head` truncation on evidence. vitest prints a `Tests  N passed (N)` line and exits non-zero on failure.
- **Never touch real data.**
  - Every new test sets `EPISODIC_MEMORY_CONFIG_DIR`, `TEST_DB_PATH` and `TEST_ARCHIVE_DIR` to a `mkdtemp` directory, and `HOME` too when it spawns `sync-cli`. Each test deletes them afterwards.
  - Nothing runs `maintain` or `sync` against `~/.config/superpowers`, `~/.claude` or `~/.codex`.
  - The real run is the operator's, after the loop.
- **`dist/` is gitignored and is not staged.** Task 13 rebuilds it in place with `npm run build`. Deployment is outside this loop: refreshing both plugin caches needs an operator go.
- **No push, no commit.** Tasks stage only. The operator commits with `/commit` after a final passing Fable review.
- **Never pattern-kill.** No step kills a process.
- **Test runner.** `/Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory/node_modules/.bin/vitest run --root /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory <file filter>`. Every step spells out the full command.
- **Type-check before every stage.** vitest does not type-check, so every code task runs `tsc --noEmit` on the whole package before staging.
  - Fix errors in the task's own `allowed_files`.
  - An error in a file outside them, for example a sibling task in the same wave mid-edit, is reported, not fixed.
- **No index builds at MCP startup on an existing index.** `initDatabase` creates `idx_session_class` and `idx_archive_path` only when it creates a new database file. On an existing (possibly 42 GB) index, `maintain` builds them under the sync lock, so concurrent MCP starts never race a long index build into `SQLITE_BUSY`.

---

## Task 1: archive-io helper (L3, L7, L8)

**Files:**
- Create: `worker/mcp-servers/episodic-memory/src/archive-io.ts`
- Test: `worker/mcp-servers/episodic-memory/src/archive-io.test.ts`

**Step 1: Write the test (RED).** Create `worker/mcp-servers/episodic-memory/src/archive-io.test.ts`:
```ts
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
});
```

**Step 2: Run the test and see it fail.**
```bash
/Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory/node_modules/.bin/vitest run --root /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory src/archive-io.test.ts
```
Expected: FAIL, non-zero exit. The suite cannot import `./archive-io.js` because the file does not exist.

**Step 3: Implement (GREEN).** Create `worker/mcp-servers/episodic-memory/src/archive-io.ts`:
```ts
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

**Step 4: Run the test and see it pass.** Run the Step 2 command.
Expected: exit 0, `Tests  7 passed (7)`. If the positive control fails, stop and report the Node version: compaction cannot ship on that runtime.

**Step 5: Type-check.**
```bash
/Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory/node_modules/.bin/tsc --noEmit -p /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory/tsconfig.json
```
Expected: no output, exit 0.

**Step 6: Stage.**
```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/mcp-servers/episodic-memory/src/archive-io.ts worker/mcp-servers/episodic-memory/src/archive-io.test.ts
```
Expected: no output, exit 0.

---

## Task 2: Route every archive reader through archive-io (L8)

**Files:**
- Modify: `worker/mcp-servers/episodic-memory/src/parser.ts:1,33`
- Modify: `worker/mcp-servers/episodic-memory/src/codex-parser.ts:1,28`
- Modify: `worker/mcp-servers/episodic-memory/src/search.ts:5-6,169-196,238-240,352-354`
- Modify: `worker/mcp-servers/episodic-memory/src/show-cli.ts:1,49`
- Modify: `worker/mcp-servers/episodic-memory/src/repair-cli.ts:12,61,66`
- Modify: `worker/mcp-servers/episodic-memory/src/mcp-server.ts:24-25,271-295`
- Modify: `worker/mcp-servers/episodic-memory/src/indexer.ts:9,18-24,115-116,223-225,316-318`
- Modify: `worker/mcp-servers/episodic-memory/src/verify.ts:1-5,50,73-80`
- Test: `worker/mcp-servers/episodic-memory/src/archive-readers.test.ts`

**Step 1: Write the test (RED).** Create `worker/mcp-servers/episodic-memory/src/archive-readers.test.ts`:
```ts
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
```

**Step 2: Run the test and see it fail.**
```bash
/Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory/node_modules/.bin/vitest run --root /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory src/archive-readers.test.ts
```
Expected: FAIL, 4 failed.
- The two parsers raise ENOENT on the missing raw `.jsonl`.
- `formatResults` fails, either by printing `(0KB, 0 lines)` or by timing out.
- `verifyIndex` lists `row-1` as orphaned.

**Step 3: `parser.ts`.**
- Replace line 1 `import fs from 'fs';` with `import { openArchiveStream } from './archive-io.js';`.
- Replace line 33 `const fileStream = fs.createReadStream(filePath);` with `const fileStream = openArchiveStream(filePath);`.

**Step 4: `codex-parser.ts`.**
- Replace line 1 `import fs from 'fs';` with `import { openArchiveStream } from './archive-io.js';`.
- Replace line 28 `input: fs.createReadStream(filePath), crlfDelay: Infinity,` with `input: openArchiveStream(filePath), crlfDelay: Infinity,`.

**Step 5: `search.ts`.**
- After line 6 (`import readline from 'readline';`), add `import { openArchiveStream } from './archive-io.js';`.
- Replace `countLines` and `getFileSizeInKB` (lines 169-196, from `// Helper function to count lines in a file efficiently` through the closing `}` of `getFileSizeInKB`) with one function. It reports the transcript's decompressed size, so the size hint that guides read pagination does not shrink 5–20× once a file is compacted:
```ts
// Count non-empty lines and the transcript's (decompressed) size in one pass.
async function fileMetrics(filePath: string): Promise<{ lines: number; kb: number }> {
  try {
    const stream = openArchiveStream(filePath);
    let bytes = 0;
    stream.on('data', (chunk: Buffer | string) => {
      bytes += typeof chunk === 'string' ? Buffer.byteLength(chunk) : chunk.length;
    });
    const rl = readline.createInterface({ input: stream, crlfDelay: Infinity });
    stream.once('error', () => rl.close()); // a decode/read error ends the loop instead of hanging
    let lines = 0;
    for await (const line of rl) {
      if (line.trim()) lines++;
    }
    return { lines, kb: Math.round(bytes / 1024 * 10) / 10 };
  } catch (error) {
    return { lines: 0, kb: 0 }; // missing or unreadable file
  }
}
```
- In both `formatResults` and `formatMultiConceptResults`, replace the two lines
```ts
    const fileSizeKB = getFileSizeInKB(result.exchange.archivePath);
    const totalLines = await countLines(result.exchange.archivePath);
```
with
```ts
    const { lines: totalLines, kb: fileSizeKB } = await fileMetrics(result.exchange.archivePath);
```

**Step 6: `show-cli.ts`.**
- Replace line 1 `import { readFileSync } from 'fs';` with `import { readArchiveText } from './archive-io.js';`.
- Replace line 49 `const jsonl = readFileSync(filePath, 'utf-8');` with `const jsonl = readArchiveText(filePath);`.

**Step 7: `repair-cli.ts`.**
- Replace line 12 `import fs from 'fs';` with `import { archiveExists, readArchiveText } from './archive-io.js';`.
- Replace line 61 `if (!fs.existsSync(archive_path)) {` with `if (!archiveExists(archive_path)) {`.
- Replace line 66 `const content = fs.readFileSync(archive_path, 'utf-8');` with `const content = readArchiveText(archive_path);`.

**Step 8: `mcp-server.ts`.**
- After line 24 (`import { getArchiveDir } from './paths.js';`), add `import { resolveArchiveFile, readArchiveText } from './archive-io.js';`.
- Replace lines 271-295 with the block below. It runs from `// Verify file exists (preserve existing not-found handling)` through `const jsonlContent = fs.readFileSync(resolvedPath, 'utf-8');`. Containment is checked on the real file that will be read:
```ts
      // Resolve the logical .jsonl to the file on disk (raw, or .jsonl.zst once compacted).
      const onDisk = resolveArchiveFile(path.resolve(params.path));
      if (!onDisk) {
        throw new Error(`File not found: ${params.path}`);
      }

      // Confine reads to the conversation-archive directory. Episodic memory
      // indexes untrusted prior-conversation content, so a prompt-injection
      // payload in an indexed exchange could otherwise steer the agent to read
      // an arbitrary local file (SSH keys, .env, etc.) and surface it. Resolve
      // both the archive base and the file on disk through realpathSync to
      // defeat symlink escapes, then assert containment before reading.
      const archiveDir = fs.realpathSync(getArchiveDir());
      const resolvedPath = fs.realpathSync(onDisk);
      const relative = path.relative(archiveDir, resolvedPath);
      const isInsideArchive =
        resolvedPath === archiveDir ||
        (relative !== '' && !relative.startsWith('..') && !path.isAbsolute(relative));
      if (!isInsideArchive) {
        throw new Error(
          `Access denied: path is outside the conversation archive directory: ${params.path}`
        );
      }

      // Read and format conversation with optional line range
      const jsonlContent = readArchiveText(resolvedPath);
```

**Step 9: `indexer.ts`.**
- After line 9 (`import { getArchiveDir, getExcludedProjects } from './paths.js';`), add `import { archiveExists, readArchiveText } from './archive-io.js';`.
- In `getFileLineCount` (lines 18-24), replace `return fs.readFileSync(filePath, 'utf-8').split('\n').length;` with `return readArchiveText(filePath).split('\n').length;`.
- At each of the three copy sites (`:115-116`, `:223-225`, `:316-318`), replace `if (!fs.existsSync(archivePath)) {` with `if (!archiveExists(archivePath)) {`, and replace `fs.copyFileSync(sourcePath, archivePath);` with `fs.copyFileSync(sourcePath, archivePath, fs.constants.COPYFILE_FICLONE);`.

**Step 10: `verify.ts`.**
- After line 5 (`import { getArchiveDir, getExcludedProjects } from './paths.js';`, unchanged), add `import { archiveStat, logicalPath } from './archive-io.js';`.
- Replace line 50 with:
```ts
    // A compacted transcript exists only as <name>.jsonl.zst; list it by its logical
    // path so its rows are not reported (and later deleted) as orphans.
    const files = Array.from(new Set(
      fs.readdirSync(projectPath)
        .filter(f => f.endsWith('.jsonl') || f.endsWith('.jsonl.zst'))
        .map(f => logicalPath(f))
    ));
```
- Replace lines 73-80, from `const fileStat = fs.statSync(conversationPath);` through the closing `}` of `if (fileStat.mtimeMs > lastIndexed) {…}`, with:
```ts
        const fileStat = archiveStat(conversationPath);
        if (fileStat && fileStat.mtimeMs > lastIndexed) {
          result.outdated.push({
            path: conversationPath,
            fileTime: fileStat.mtimeMs,
            dbTime: lastIndexed
          });
        }
```

**Step 11: Run the test and see it pass.** Run the Step 2 command.
Expected: exit 0, `Tests  4 passed (4)`.

**Step 12: Run the existing parser tests.**
```bash
/Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory/node_modules/.bin/vitest run --root /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory src/codex-parser.test.ts src/codex-sync.test.ts
```
Expected: exit 0, `Tests  6 passed (6)`.

**Step 13: Type-check.**
```bash
/Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory/node_modules/.bin/tsc --noEmit -p /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory/tsconfig.json
```
Expected: no output, exit 0.

**Step 14: Stage.**
```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/mcp-servers/episodic-memory/src/parser.ts worker/mcp-servers/episodic-memory/src/codex-parser.ts worker/mcp-servers/episodic-memory/src/search.ts worker/mcp-servers/episodic-memory/src/show-cli.ts worker/mcp-servers/episodic-memory/src/repair-cli.ts worker/mcp-servers/episodic-memory/src/mcp-server.ts worker/mcp-servers/episodic-memory/src/indexer.ts worker/mcp-servers/episodic-memory/src/verify.ts worker/mcp-servers/episodic-memory/src/archive-readers.test.ts
```
Expected: no output, exit 0.

---

## Task 3: db.ts — no tool_calls; entrypoint, session_class and meta (L1, L2)

**Files:**
- Modify: `worker/mcp-servers/episodic-memory/src/db.ts`. Replace the whole file (239 lines).
- Modify: `worker/mcp-servers/episodic-memory/src/types.ts:11-38`
- Test: `worker/mcp-servers/episodic-memory/src/db.test.ts`

**Step 1: Write the test (RED).** Create `worker/mcp-servers/episodic-memory/src/db.test.ts`:
```ts
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import fs from 'fs';
import os from 'os';
import path from 'path';
import Database from 'better-sqlite3';
import * as sqliteVec from 'sqlite-vec';

let tmp: string;
const VEC = new Array(384).fill(0.01);
const names = (db: Database.Database, type: string) =>
  (db.prepare(`SELECT name FROM sqlite_master WHERE type = ?`).all(type) as Array<{ name: string }>).map(r => r.name);
const columns = (db: Database.Database) =>
  (db.prepare(`SELECT name FROM pragma_table_info('exchanges')`).all() as Array<{ name: string }>).map(r => r.name);

beforeEach(async () => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'db-lean-'));
  process.env.EPISODIC_MEMORY_CONFIG_DIR = path.join(tmp, 'config');
  process.env.TEST_DB_PATH = path.join(tmp, 'db.sqlite');
  process.env.TEST_ARCHIVE_DIR = path.join(tmp, 'archive');
  const mod: any = await import('./db.js');
  mod.resetDatabaseCache?.();
});

afterEach(async () => {
  const mod: any = await import('./db.js');
  mod.resetDatabaseCache?.();
  fs.rmSync(tmp, { recursive: true, force: true });
  delete process.env.EPISODIC_MEMORY_CONFIG_DIR;
  delete process.env.TEST_DB_PATH;
  delete process.env.TEST_ARCHIVE_DIR;
});

describe('db schema (lean)', () => {
  it('creates no tool_calls table or tool indexes, and has meta, provenance columns and indexes', async () => {
    const { initDatabase } = await import('./db.js');
    const db = initDatabase();
    expect(names(db, 'table')).not.toContain('tool_calls');
    expect(names(db, 'index')).not.toContain('idx_tool_name');
    expect(names(db, 'index')).not.toContain('idx_tool_exchange');
    expect(names(db, 'table')).toContain('meta');
    expect(columns(db)).toEqual(expect.arrayContaining(['entrypoint', 'session_class']));
    expect(names(db, 'index')).toEqual(expect.arrayContaining(['idx_session_class', 'idx_archive_path']));
  });

  it('insertExchange stores entrypoint and session_class and writes no tool rows', async () => {
    const { initDatabase, insertExchange } = await import('./db.js');
    const db = initDatabase();
    insertExchange(db, {
      id: 'e1', project: 'p', timestamp: '2026-10-01T00:00:00.000Z', userMessage: 'u', assistantMessage: 'a',
      archivePath: '/x.jsonl', lineStart: 1, lineEnd: 2, entrypoint: 'cli', sessionClass: 'operator',
      toolCalls: [{ id: 't1', exchangeId: 'e1', toolName: 'Write', toolInput: { big: 'x'.repeat(1000) }, isError: false, timestamp: '2026-10-01T00:00:00.000Z' }],
    } as any, VEC, ['Write']);
    expect(db.prepare(`SELECT entrypoint, session_class FROM exchanges WHERE id = 'e1'`).get())
      .toEqual({ entrypoint: 'cli', session_class: 'operator' });
    expect(names(db, 'table')).not.toContain('tool_calls');
  });

  it('migrates a legacy exchanges table additively and keeps its rows', async () => {
    const legacy = new Database(process.env.TEST_DB_PATH!);
    sqliteVec.load(legacy);
    legacy.exec(`CREATE TABLE exchanges (id TEXT PRIMARY KEY, project TEXT NOT NULL, timestamp TEXT NOT NULL, user_message TEXT NOT NULL, assistant_message TEXT NOT NULL, archive_path TEXT NOT NULL, line_start INTEGER NOT NULL, line_end INTEGER NOT NULL, embedding BLOB)`);
    legacy.prepare(`INSERT INTO exchanges VALUES ('old1','p','2026-01-01T00:00:00.000Z','u','a','/x.jsonl',1,2,NULL)`).run();
    legacy.close();
    const { initDatabase } = await import('./db.js');
    const db = initDatabase();
    expect(columns(db)).toEqual(expect.arrayContaining(['session_id', 'entrypoint', 'session_class']));
    expect(db.prepare(`SELECT COUNT(*) AS c FROM exchanges`).get()).toEqual({ c: 1 });
    // Startup stays cheap on an existing index: maintain builds these under the lock.
    expect(names(db, 'index')).not.toContain('idx_archive_path');
    expect(names(db, 'index')).not.toContain('idx_session_class');
  });

  it('meta helpers: null before the table exists, upsert after', async () => {
    const mod: any = await import('./db.js');
    const raw = new Database(path.join(tmp, 'plain.sqlite'));
    expect(mod.getMeta(raw, 'slim_done')).toBeNull();
    mod.setMeta(raw, 'slim_done', '1');
    mod.setMeta(raw, 'slim_done', '2');
    expect(mod.getMeta(raw, 'slim_done')).toBe('2');
    expect(mod.tableExists(raw, 'meta')).toBe(true);
    raw.close();
  });
});
```

**Step 2: Run the test and see it fail.**
```bash
/Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory/node_modules/.bin/vitest run --root /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory src/db.test.ts
```
Expected: FAIL, 4 failed.
- `tool_calls` exists.
- The `entrypoint` column is missing.
- The legacy migration lacks the new columns.
- `getMeta` is not a function.

**Step 3: `types.ts`.** In `ConversationExchange`, after `thinkingTriggers?: string; // JSON array` (line 34), add:
```ts

  // Provenance (L2): Claude `entrypoint` (cli, sdk-py, sdk-cli) or Codex
  // session_meta.payload.originator (Codex Desktop, codex_exec, ironclaude).
  entrypoint?: string;
  // operator | brain | worker | machine | archive-only | unknown (settings.ts classifySession)
  sessionClass?: string;
  // True when userMessage holds AskUserQuestion answers ("Q: … / A: …").
  isAnswer?: boolean;
```

**Step 4: Implement `db.ts` (GREEN).** Replace the whole file with:
```ts
import Database from 'better-sqlite3';
import { ConversationExchange } from './types.js';
import path from 'path';
import fs from 'fs';
import * as sqliteVec from 'sqlite-vec';
import { getDbPath } from './paths.js';

export function migrateSchema(db: Database.Database): void {
  const columns = db.prepare(`SELECT name FROM pragma_table_info('exchanges')`).all() as Array<{ name: string }>;
  const columnNames = new Set(columns.map(c => c.name));

  const migrations: Array<{ name: string; sql: string }> = [
    { name: 'last_indexed', sql: 'ALTER TABLE exchanges ADD COLUMN last_indexed INTEGER' },
    { name: 'parent_uuid', sql: 'ALTER TABLE exchanges ADD COLUMN parent_uuid TEXT' },
    { name: 'is_sidechain', sql: 'ALTER TABLE exchanges ADD COLUMN is_sidechain BOOLEAN DEFAULT 0' },
    { name: 'session_id', sql: 'ALTER TABLE exchanges ADD COLUMN session_id TEXT' },
    { name: 'cwd', sql: 'ALTER TABLE exchanges ADD COLUMN cwd TEXT' },
    { name: 'git_branch', sql: 'ALTER TABLE exchanges ADD COLUMN git_branch TEXT' },
    { name: 'claude_version', sql: 'ALTER TABLE exchanges ADD COLUMN claude_version TEXT' },
    { name: 'thinking_level', sql: 'ALTER TABLE exchanges ADD COLUMN thinking_level TEXT' },
    { name: 'thinking_disabled', sql: 'ALTER TABLE exchanges ADD COLUMN thinking_disabled BOOLEAN' },
    { name: 'thinking_triggers', sql: 'ALTER TABLE exchanges ADD COLUMN thinking_triggers TEXT' },
    { name: 'entrypoint', sql: 'ALTER TABLE exchanges ADD COLUMN entrypoint TEXT' },
    { name: 'session_class', sql: 'ALTER TABLE exchanges ADD COLUMN session_class TEXT' },
  ];

  let migrated = false;
  for (const migration of migrations) {
    if (!columnNames.has(migration.name)) {
      console.log(`Migrating schema: adding ${migration.name} column...`);
      db.prepare(migration.sql).run();
      migrated = true;
    }
  }

  if (migrated) {
    console.log('Migration complete.');
  }
}

/**
 * idx_session_class and idx_archive_path. Building them scans every exchanges row, so
 * initDatabase builds them only for a brand-new file; on an existing index `maintain`
 * builds them under the sync lock (concurrent MCP starts must not race a long build).
 */
export function ensureProvenanceIndexes(db: Database.Database): void {
  db.exec(`CREATE INDEX IF NOT EXISTS idx_session_class ON exchanges(session_class)`);
  db.exec(`CREATE INDEX IF NOT EXISTS idx_archive_path ON exchanges(archive_path)`);
}

/**
 * Every table and index the index needs. There is deliberately no tool_calls table:
 * it was write-only (nothing in src/ read it) and held ~82% of a 42 GB index. Tool
 * names still reach the embedding text through the callers of insertExchange.
 */
export function applySchema(db: Database.Database, opts: { provenanceIndexes?: boolean } = {}): void {
  db.exec(`
    CREATE TABLE IF NOT EXISTS exchanges (
      id TEXT PRIMARY KEY,
      project TEXT NOT NULL,
      timestamp TEXT NOT NULL,
      user_message TEXT NOT NULL,
      assistant_message TEXT NOT NULL,
      archive_path TEXT NOT NULL,
      line_start INTEGER NOT NULL,
      line_end INTEGER NOT NULL,
      embedding BLOB,
      last_indexed INTEGER,
      parent_uuid TEXT,
      is_sidechain BOOLEAN DEFAULT 0,
      session_id TEXT,
      cwd TEXT,
      git_branch TEXT,
      claude_version TEXT,
      thinking_level TEXT,
      thinking_disabled BOOLEAN,
      thinking_triggers TEXT,
      entrypoint TEXT,
      session_class TEXT
    )
  `);

  db.exec(`
    CREATE VIRTUAL TABLE IF NOT EXISTS vec_exchanges USING vec0(
      id TEXT PRIMARY KEY,
      embedding FLOAT[384]
    )
  `);

  db.exec(`CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)`);

  // Run migrations before indexes so every indexed column exists.
  migrateSchema(db);

  db.exec(`CREATE INDEX IF NOT EXISTS idx_timestamp ON exchanges(timestamp DESC)`);
  db.exec(`CREATE INDEX IF NOT EXISTS idx_session_id ON exchanges(session_id)`);
  db.exec(`CREATE INDEX IF NOT EXISTS idx_project ON exchanges(project)`);
  db.exec(`CREATE INDEX IF NOT EXISTS idx_sidechain ON exchanges(is_sidechain)`);
  db.exec(`CREATE INDEX IF NOT EXISTS idx_git_branch ON exchanges(git_branch)`);
  if (opts.provenanceIndexes) ensureProvenanceIndexes(db);
}

let _db: Database.Database | null = null;

/** Close and forget the cached handle (used after the slim swaps db.sqlite, and by tests). */
export function resetDatabaseCache(): void {
  if (_db && _db.open) _db.close();
  _db = null;
}

export function initDatabase(): Database.Database {
  // Reuse the cached handle only if it is still open. better-sqlite3 exposes a
  // `.open` boolean that flips to false after `.close()`. A closed handle would
  // throw "database is not open" on the next query, so drop it and re-open.
  if (_db && _db.open) return _db;
  _db = null;
  const dbPath = getDbPath();

  const dbDir = path.dirname(dbPath);
  if (!fs.existsSync(dbDir)) {
    fs.mkdirSync(dbDir, { recursive: true });
  }

  const isNewFile = !fs.existsSync(dbPath);
  const db = new Database(dbPath);
  sqliteVec.load(db);
  db.pragma('journal_mode = WAL');
  applySchema(db, { provenanceIndexes: isNewFile });

  _db = db;
  return db;
}

export function tableExists(db: Database.Database, name: string): boolean {
  return !!db.prepare(`SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?`).get(name);
}

export function getMeta(db: Database.Database, key: string): string | null {
  if (!tableExists(db, 'meta')) return null;
  const row = db.prepare(`SELECT value FROM meta WHERE key = ?`).get(key) as { value: string } | undefined;
  return row ? row.value : null;
}

export function setMeta(db: Database.Database, key: string, value: string): void {
  db.exec(`CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)`);
  db.prepare(`INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value`)
    .run(key, value);
}

export function insertExchange(
  db: Database.Database,
  exchange: ConversationExchange,
  embedding: number[],
  _toolNames?: string[]
): void {
  const now = Date.now();

  // One transaction, so an exchanges row never exists without its vec_exchanges row.
  const txn = db.transaction(() => {
    db.prepare(`
      INSERT OR REPLACE INTO exchanges
      (id, project, timestamp, user_message, assistant_message, archive_path, line_start, line_end, last_indexed,
       parent_uuid, is_sidechain, session_id, cwd, git_branch, claude_version,
       thinking_level, thinking_disabled, thinking_triggers, entrypoint, session_class)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    `).run(
      exchange.id,
      exchange.project,
      exchange.timestamp,
      exchange.userMessage,
      exchange.assistantMessage,
      exchange.archivePath,
      exchange.lineStart,
      exchange.lineEnd,
      now,
      exchange.parentUuid || null,
      exchange.isSidechain ? 1 : 0,
      exchange.sessionId || null,
      exchange.cwd || null,
      exchange.gitBranch || null,
      exchange.claudeVersion || null,
      exchange.thinkingLevel || null,
      exchange.thinkingDisabled ? 1 : 0,
      exchange.thinkingTriggers || null,
      exchange.entrypoint || null,
      exchange.sessionClass || null
    );

    // Virtual tables don't support REPLACE: delete, then insert.
    db.prepare(`DELETE FROM vec_exchanges WHERE id = ?`).run(exchange.id);
    db.prepare(`INSERT INTO vec_exchanges (id, embedding) VALUES (?, ?)`)
      .run(exchange.id, Buffer.from(new Float32Array(embedding).buffer));
  });

  txn();
}

export function getAllExchanges(db: Database.Database): Array<{ id: string; archivePath: string }> {
  const stmt = db.prepare(`SELECT id, archive_path as archivePath FROM exchanges`);
  return stmt.all() as Array<{ id: string; archivePath: string }>;
}

export function getFileLastIndexed(db: Database.Database, archivePath: string): number | null {
  const stmt = db.prepare(`
    SELECT MAX(last_indexed) as lastIndexed
    FROM exchanges
    WHERE archive_path = ?
  `);
  const row = stmt.get(archivePath) as { lastIndexed: number | null };
  return row.lastIndexed;
}

export function deleteExchange(db: Database.Database, id: string): void {
  db.prepare(`DELETE FROM vec_exchanges WHERE id = ?`).run(id);
  db.prepare(`DELETE FROM exchanges WHERE id = ?`).run(id);
}
```

**Step 5: Run the test and see it pass.** Run the Step 2 command.
Expected: exit 0, `Tests  4 passed (4)`.

**Step 6: Prove that only `db.ts` ever named the table (L1).**
```bash
grep -rn "tool_calls" /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory/src '--include=*.ts'
```
Expected: matches only in `db.test.ts` (its assertions) and `db.ts` (the comment in `applySchema`). No SQL anywhere creates, inserts into or indexes `tool_calls`.

**Step 7: Type-check.**
```bash
/Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory/node_modules/.bin/tsc --noEmit -p /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory/tsconfig.json
```
Expected: no output, exit 0.

**Step 8: Stage.**
```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/mcp-servers/episodic-memory/src/db.ts worker/mcp-servers/episodic-memory/src/types.ts worker/mcp-servers/episodic-memory/src/db.test.ts
```
Expected: no output, exit 0.

---

## Task 4: Settings file and session classifier (L2, L9, L10, L11, L15)

**Files:**
- Create: `worker/mcp-servers/episodic-memory/src/settings.ts`
- Test: `worker/mcp-servers/episodic-memory/src/settings.test.ts`

**Step 1: Write the test (RED).** Create `worker/mcp-servers/episodic-memory/src/settings.test.ts`:
```ts
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import fs from 'fs';
import os from 'os';
import path from 'path';
import {
  DEFAULT_SETTINGS, loadSettings, classifySession, isMachineProject, readFirstUserText, getSettingsPath,
} from './settings.js';

let tmp: string;
beforeEach(() => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'em-settings-'));
  process.env.EPISODIC_MEMORY_CONFIG_DIR = path.join(tmp, 'config');
});
afterEach(() => {
  fs.rmSync(tmp, { recursive: true, force: true });
  delete process.env.EPISODIC_MEMORY_CONFIG_DIR;
});

const CASES: Array<[string, string | undefined, string]> = [
    ['-Users-alice--ironclaude-grader', undefined, 'machine'],
    ['-Users-alice--tron-grader', undefined, 'machine'],
    ['-private-var-folders-02-3n1lytx537v4l1dknbj7892w0000gp-T-claude-judge--3wdjdv1', undefined, 'machine'],
    ['-private-tmp', undefined, 'machine'],
    ['-private-tmp-claude-502--Users-alice-Code-app-scratchpad-probe', 'cli', 'machine'],
    ['-var-folders-02-abc-T-tmp-BRUONHDqhq', 'cli', 'machine'],
    ['-tmp-scratch', 'cli', 'machine'],
    ['-Users-alice--ironclaude-brain', undefined, 'brain'],
    ['-Users-alice-Code-repo--ironclaude-worktrees-352afa8c-9e57-42c1-bada-de222093de5c', 'sdk-py', 'worker'],
    ['-Users-alice-Code-repo', 'cli', 'operator'],
    ['-Users-alice-Code-repo', 'Codex Desktop', 'operator'],
    ['-Users-alice-Code-repo', 'codex-tui', 'operator'],
    ['-Users-alice-Code-repo', 'sdk-py', 'worker'],
    ['-Users-alice-Code-repo', 'codex_exec', 'worker'],
    ['-Users-alice-Code-repo', 'ironclaude', 'worker'],
    ['-Users-alice-Code-repo', 'some-new-ide', 'unknown'],
    ['-Users-alice-Code-repo', undefined, 'unknown'],
    ['-Users-alice-Code-autograder', 'cli', 'operator'],
    ['-Users-alice-Code-tmp-tools', 'cli', 'operator'],
    ['-Users-alice-Code-rp-knowledge-base-programmatically-control-claude-work-chunk-CHUNK-PZO2105-APG-112-131', 'sdk-py', 'archive-only'],
    ['-Users-alice-Code-rp-knowledge-base-programmatically-control-claude', 'cli', 'operator'],
];

describe('classifySession with the shipped defaults (real directory shapes, any user name)', () => {
  it.each(CASES)('%s + %s -> %s', (project, entrypoint, expected) => {
    expect(classifySession({ project, entrypoint }, DEFAULT_SETTINGS)).toBe(expected);
  });

  it('first-user-text prefixes mark probe and automated-review sessions as machine, and nothing else', () => {
    const p = '-Users-alice-Code-repo';
    expect(classifySession({ project: p, entrypoint: 'sdk-cli', firstUserText: 'Output only your exact model id string and nothing else.' }, DEFAULT_SETTINGS)).toBe('machine');
    expect(classifySession({ project: p, entrypoint: 'sdk-py', firstUserText: 'Review this change for security vulnerabilities.\n\nChanged files' }, DEFAULT_SETTINGS)).toBe('machine');
    expect(classifySession({ project: p, entrypoint: 'cli', firstUserText: 'Please review this change' }, DEFAULT_SETTINGS)).toBe('operator');
  });

  it('defaults name no user', () => {
    expect(JSON.stringify(DEFAULT_SETTINGS)).not.toContain(os.userInfo().username);
  });

  it('the automatic maintain run is on by default (L16)', () => {
    expect(DEFAULT_SETTINGS.maintain.autoRun).toBe(true);
    expect(loadSettings().maintain.autoRun).toBe(true);
  });
});

describe('loadSettings', () => {
  it('returns defaults when the file is absent; file arrays replace default arrays', () => {
    expect(getSettingsPath()).toBe(path.join(tmp, 'config', 'episodic-memory.json'));
    expect(loadSettings()).toEqual(DEFAULT_SETTINGS);
    fs.writeFileSync(getSettingsPath(), JSON.stringify({
      exclude: { machineProjectPatterns: ['-scratchy$'] },
      maintain: { autoRun: false },
      backup: { mirrorDir: '/Volumes/Backup/em' },
    }));
    const s = loadSettings();
    expect(isMachineProject('-Users-a-scratchy', s)).toBe(true);
    expect(isMachineProject('-Users-a--ironclaude-grader', s)).toBe(false);
    expect(s.maintain.autoRun).toBe(false);
    expect(s.maintain.liveWindowHours).toBe(24);
    expect(s.backup.mirrorDir).toBe('/Volumes/Backup/em');
    expect(s.exclude.archiveSubagents).toBe(true);
  });

  it('invalid JSON keeps default rules but disables the automatic maintain run', () => {
    fs.mkdirSync(path.dirname(getSettingsPath()), { recursive: true });
    fs.writeFileSync(getSettingsPath(), '{not json');
    const s = loadSettings();
    expect(s.maintain.autoRun).toBe(false);
    expect(s.exclude).toEqual(DEFAULT_SETTINGS.exclude);
  });

  it('readFirstUserText returns the first user text and skips other records', async () => {
    const f = path.join(tmp, 's.jsonl');
    fs.writeFileSync(f, [
      JSON.stringify({ type: 'queue-operation', operation: 'enqueue' }),
      JSON.stringify({ type: 'user', message: { role: 'user', content: [{ type: 'tool_result', content: 'x' }] } }),
      JSON.stringify({ type: 'user', message: { role: 'user', content: [{ type: 'text', text: 'Hello there' }] } }),
    ].join('\n') + '\n');
    expect(await readFirstUserText(f)).toBe('Hello there');
    const g = path.join(tmp, 'none.jsonl');
    fs.writeFileSync(g, JSON.stringify({ type: 'summary' }) + '\n');
    expect(await readFirstUserText(g)).toBeUndefined();
  });
});
```

**Step 2: Run the test and see it fail.**
```bash
/Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory/node_modules/.bin/vitest run --root /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory src/settings.test.ts
```
Expected: FAIL, non-zero exit. The suite cannot import `./settings.js`.

**Step 3: Implement (GREEN).** Create `worker/mcp-servers/episodic-memory/src/settings.ts`:
```ts
/**
 * settings.ts — episodic-memory's own settings and the session classifier.
 *
 * File: <getSuperpowersDir()>/episodic-memory.json (EPISODIC_MEMORY_CONFIG_DIR
 * overrides the directory). Every key is optional; missing keys use
 * DEFAULT_SETTINGS. An array in the file REPLACES the default array.
 *
 * Nothing here names a person. "operator" means an interactive session: Claude
 * entrypoint `cli`, Codex originator `Codex Desktop` or `codex-tui`. It is derived
 * from transcript metadata, so it works for any operator.
 */
import fs from 'fs';
import path from 'path';
import readline from 'readline';
import { getSuperpowersDir } from './paths.js';

// 'archive-only': archived (and compacted) but never indexed, like subagent transcripts.
export type SessionClass = 'operator' | 'brain' | 'worker' | 'machine' | 'archive-only' | 'unknown';

export interface EpisodicSettings {
  maintain: {
    autoRun: boolean;          // default true for every install (L16); guards still apply
    liveWindowHours: number;   // archive files modified more recently are not compacted
    zstdLevel: number;
    maxRuntimeMinutes: number; // sync worker runtime budget (watchdog); lock staleness is 2x this
  };
  exclude: {
    machineProjectPatterns: string[];       // regexes on the project directory name
    machineFirstUserTextPrefixes: string[]; // first user message prefixes of machine sessions
    archiveSubagents: boolean;              // archive <proj>/<session>/subagents/*.jsonl (never indexed)
    archiveOnlyProjectPatterns: string[];   // regexes: archived + compacted, never indexed or summarized
  };
  classify: {
    brainProjectPatterns: string[];
    workerProjectPatterns: string[];
    operatorEntrypoints: string[];            // interactive surfaces
    automationEntrypoints: string[];          // exact automation values
    automationEntrypointPrefixes: string[];   // e.g. 'sdk-' (sdk-py, sdk-cli)
  };
  backup: {
    mirrorDir: string | null;  // off by default; local or mounted directory only
  };
}

export const DEFAULT_SETTINGS: EpisodicSettings = {
  maintain: { autoRun: true, liveWindowHours: 24, zstdLevel: 3, maxRuntimeMinutes: 120 },
  exclude: {
    machineProjectPatterns: [
      '--(ironclaude|tron)-grader$',  // Commander grader home (~/.ironclaude/grader)
      'claude-judge-',                // continuation-judge temp dirs
      '^-private-tmp($|-)',           // macOS /tmp scratch, including summarizer sessions
      '^-tmp($|-)',                   // Linux /tmp scratch
      '^-(private-)?var-folders-',    // macOS $TMPDIR test harnesses and judges
    ],
    machineFirstUserTextPrefixes: [
      'Output only your exact model id string and nothing else.',  // claude -p model probes (sdk-cli)
      'Review this change for security vulnerabilities.',          // automated security reviews (sdk-py)
    ],
    archiveSubagents: true,
    archiveOnlyProjectPatterns: [
      '-programmatically-control-claude-work-chunk-',  // automated knowledge-base work-chunk pipeline sessions
    ],
  },
  classify: {
    brainProjectPatterns: ['--(ironclaude|tron)-brain$'],  // Commander brain_cwd default ~/.ironclaude/brain
    workerProjectPatterns: ['--ironclaude-worktrees-'],   // managed worker worktrees
    operatorEntrypoints: ['cli', 'Codex Desktop', 'codex-tui'],
    automationEntrypoints: ['codex_exec', 'ironclaude'],
    automationEntrypointPrefixes: ['sdk-'],
  },
  backup: { mirrorDir: null },
};

export function getSettingsPath(): string {
  return path.join(getSuperpowersDir(), 'episodic-memory.json');
}

function merge<T extends object>(base: T, over: unknown): T {
  if (!over || typeof over !== 'object' || Array.isArray(over)) return base;
  const out: Record<string, unknown> = { ...(base as Record<string, unknown>) };
  for (const [key, value] of Object.entries(over as Record<string, unknown>)) {
    if (!(key in out)) continue;
    const current = out[key];
    const bothObjects = current && typeof current === 'object' && !Array.isArray(current)
      && value && typeof value === 'object' && !Array.isArray(value);
    out[key] = bothObjects ? merge(current as object, value) : value;
  }
  return out as T;
}

export function loadSettings(): EpisodicSettings {
  const file = getSettingsPath();
  if (!fs.existsSync(file)) return DEFAULT_SETTINGS;
  try {
    return merge(DEFAULT_SETTINGS, JSON.parse(fs.readFileSync(file, 'utf-8')));
  } catch (error) {
    console.warn(`[episodic-memory] ${file} is not valid JSON; using default rules with automatic maintain disabled: ${error instanceof Error ? error.message : String(error)}`);
    return { ...DEFAULT_SETTINGS, maintain: { ...DEFAULT_SETTINGS.maintain, autoRun: false } };
  }
}

function matchesAny(value: string, patterns: string[]): boolean {
  return patterns.some(pattern => {
    try { return new RegExp(pattern).test(value); } catch { return false; }
  });
}

export function isMachineProject(project: string, settings: EpisodicSettings): boolean {
  return matchesAny(project, settings.exclude.machineProjectPatterns);
}

export function hasMachinePrefix(firstUserText: string | undefined, settings: EpisodicSettings): boolean {
  return !!firstUserText && settings.exclude.machineFirstUserTextPrefixes.some(p => firstUserText.startsWith(p));
}

export function isArchiveOnlyProject(project: string, settings: EpisodicSettings): boolean {
  return matchesAny(project, settings.exclude.archiveOnlyProjectPatterns);
}

/** Classes that never appear in the index (machine is also never archived). */
export const UNINDEXED_CLASSES: SessionClass[] = ['machine', 'archive-only'];

export function classifySession(
  s: { project: string; entrypoint?: string | null; firstUserText?: string },
  settings: EpisodicSettings
): SessionClass {
  if (isMachineProject(s.project, settings) || hasMachinePrefix(s.firstUserText, settings)) return 'machine';
  if (isArchiveOnlyProject(s.project, settings)) return 'archive-only';
  if (matchesAny(s.project, settings.classify.brainProjectPatterns)) return 'brain';
  if (matchesAny(s.project, settings.classify.workerProjectPatterns)) return 'worker';
  const ep = s.entrypoint;
  if (ep && settings.classify.operatorEntrypoints.includes(ep)) return 'operator';
  if (ep && (settings.classify.automationEntrypoints.includes(ep)
    || settings.classify.automationEntrypointPrefixes.some(p => ep.startsWith(p)))) return 'worker';
  return 'unknown'; // no provenance (older transcripts) or a surface not seen yet: never guessed
}

/** First user message text of a Claude transcript, scanning at most `maxLines` lines. */
export async function readFirstUserText(file: string, maxLines = 200): Promise<string | undefined> {
  const input = fs.createReadStream(file);
  const rl = readline.createInterface({ input, crlfDelay: Infinity });
  let n = 0;
  try {
    for await (const line of rl) {
      if (++n > maxLines) break;
      let rec: any;
      try { rec = JSON.parse(line); } catch { continue; }
      if (rec?.type !== 'user' || !rec.message) continue;
      const content = rec.message.content;
      if (typeof content === 'string') return content;
      if (Array.isArray(content)) {
        const text = content
          .filter((b: any) => b?.type === 'text' && typeof b.text === 'string')
          .map((b: any) => b.text).join('\n');
        if (text) return text;
      }
    }
  } finally {
    rl.close();
    input.destroy();
  }
  return undefined;
}
```

**Step 4: Run the test and see it pass.** Run the Step 2 command.
Expected: exit 0, `Tests  27 passed (27)`.

**Step 5: Type-check.**
```bash
/Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory/node_modules/.bin/tsc --noEmit -p /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory/tsconfig.json
```
Expected: no output, exit 0.

**Step 6: Stage.**
```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/mcp-servers/episodic-memory/src/settings.ts worker/mcp-servers/episodic-memory/src/settings.test.ts
```
Expected: no output, exit 0.

---

## Task 5: Parsers capture AskUserQuestion answers and the entrypoint (L2)

**Files:**
- Modify: `worker/mcp-servers/episodic-memory/src/parser.ts`. The anchors are listed below; Task 2 already changed lines 1 and 33.
- Modify: `worker/mcp-servers/episodic-memory/src/codex-parser.ts`. Add `originator`.
- Create: `worker/mcp-servers/episodic-memory/src/__fixtures__/claude-askuser-session.jsonl`
- Test: `worker/mcp-servers/episodic-memory/src/parser-voice.test.ts`

**Step 1: Create the fixture.** Write `worker/mcp-servers/episodic-memory/src/__fixtures__/claude-askuser-session.jsonl` with exactly these 5 lines:
```
{"type":"queue-operation","operation":"enqueue","timestamp":"2026-10-01T00:00:00.000Z","sessionId":"22222222-2222-4222-8222-222222222222"}
{"parentUuid":null,"isSidechain":false,"userType":"external","cwd":"/Users/alice/Code/app","sessionId":"22222222-2222-4222-8222-222222222222","version":"2.1.90","gitBranch":"main","entrypoint":"cli","type":"user","message":{"role":"user","content":"Plan the KIWI migration"},"uuid":"u1","timestamp":"2026-10-01T00:00:01.000Z"}
{"parentUuid":"u1","isSidechain":false,"cwd":"/Users/alice/Code/app","sessionId":"22222222-2222-4222-8222-222222222222","version":"2.1.90","entrypoint":"cli","type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"I need one decision first."},{"type":"tool_use","id":"toolu_1","name":"AskUserQuestion","input":{"questions":[{"question":"Which scope?","header":"Scope","options":[{"label":"Retrieval only"},{"label":"Everything"}],"multiSelect":false}]}}]},"uuid":"a1","timestamp":"2026-10-01T00:00:02.000Z"}
{"parentUuid":"a1","isSidechain":false,"cwd":"/Users/alice/Code/app","sessionId":"22222222-2222-4222-8222-222222222222","version":"2.1.90","entrypoint":"cli","type":"user","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"toolu_1","content":"User has answered your questions."}]},"toolUseResult":{"questions":[{"question":"Which scope?","header":"Scope","options":[{"label":"Retrieval only"},{"label":"Everything"}],"multiSelect":false}],"answers":{"Which scope?":"Retrieval only"}},"uuid":"u2","timestamp":"2026-10-01T00:00:03.000Z"}
{"parentUuid":"u2","isSidechain":false,"cwd":"/Users/alice/Code/app","sessionId":"22222222-2222-4222-8222-222222222222","version":"2.1.90","entrypoint":"cli","type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"Proceeding with retrieval only."}]},"uuid":"a2","timestamp":"2026-10-01T00:00:04.000Z"}
```

**Step 2: Write the test (RED).** Create `worker/mcp-servers/episodic-memory/src/parser-voice.test.ts`:
```ts
import { describe, it, expect } from 'vitest';
import fs from 'fs';
import os from 'os';
import path from 'path';
import { fileURLToPath } from 'url';

const here = path.dirname(fileURLToPath(import.meta.url));
const FIXTURE = path.join(here, '__fixtures__', 'claude-askuser-session.jsonl');
const CODEX_FIXTURE = path.join(here, '__fixtures__', 'codex-rollout-sample.jsonl');

describe('operator voice in the Claude parser', () => {
  it('indexes AskUserQuestion answers as a Q→A user message that starts a new exchange', async () => {
    const { parseConversation } = await import('./parser.js');
    const ex = await parseConversation(FIXTURE, 'proj', FIXTURE);
    expect(ex.length).toBe(2);
    expect(ex[0].userMessage).toBe('Plan the KIWI migration');
    expect(ex[0].assistantMessage).toBe('I need one decision first.');
    expect(ex[0].isAnswer).toBeUndefined();
    expect(ex[1].userMessage).toBe('Q: Which scope?\nA: Retrieval only');
    expect(ex[1].assistantMessage).toBe('Proceeding with retrieval only.');
    expect(ex[1].isAnswer).toBe(true);
    expect(ex[1].lineStart).toBe(4);
  });

  it('captures the session entrypoint on every exchange', async () => {
    const { parseConversation } = await import('./parser.js');
    const ex = await parseConversation(FIXTURE, 'proj', FIXTURE);
    expect(ex.map(e => e.entrypoint)).toEqual(['cli', 'cli']);
  });

  it('formats multi-select and non-string answers; ignores other tool results', async () => {
    const { formatAnswers } = await import('./parser.js');
    expect(formatAnswers({ answers: { 'Pick tools?': ['grep', 'rg'] } })).toBe('Q: Pick tools?\nA: grep, rg');
    expect(formatAnswers({ answers: { A: 'x', B: 'y' } })).toBe('Q: A\nA: x\n\nQ: B\nA: y');
    expect(formatAnswers({ answers: { N: 3 } })).toBe('Q: N\nA: 3');
    expect(formatAnswers('Answered')).toBe('');
    expect(formatAnswers({ questions: [] })).toBe('');
    expect(formatAnswers(undefined)).toBe('');
  });

  it('leaves entrypoint undefined when the transcript has none (older CLI, Brain)', async () => {
    const { parseConversation } = await import('./parser.js');
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'parser-voice-'));
    const f = path.join(tmp, 's.jsonl');
    fs.writeFileSync(f, [
      JSON.stringify({ type: 'user', message: { role: 'user', content: 'hi' }, timestamp: '2026-10-01T00:00:00.000Z' }),
      JSON.stringify({ type: 'assistant', message: { role: 'assistant', content: [{ type: 'text', text: 'hello' }] }, timestamp: '2026-10-01T00:00:01.000Z' }),
    ].join('\n') + '\n');
    const ex = await parseConversation(f, 'p', f);
    expect(ex.length).toBe(1);
    expect(ex[0].entrypoint).toBeUndefined();
    fs.rmSync(tmp, { recursive: true, force: true });
  });
});

describe('Codex provenance', () => {
  it('records session_meta.payload.originator as the entrypoint', async () => {
    const { parseCodexConversation } = await import('./codex-parser.js');
    const ex = await parseCodexConversation(CODEX_FIXTURE, 'p', CODEX_FIXTURE);
    expect(ex[0].entrypoint).toBe('Codex Desktop');
  });
});
```

**Step 3: Run the test and see it fail.**
```bash
/Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory/node_modules/.bin/vitest run --root /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory src/parser-voice.test.ts
```
Expected: FAIL. Test 1 gets 1 exchange instead of 2. Test 2 gets an `undefined` entrypoint. Test 3 finds `formatAnswers` is not a function. Test 5 gets an `undefined` entrypoint. Test 4 passes, which is correct both before and after the change.

**Step 4: Edit `parser.ts` (GREEN).** Make these edits, each at the exact anchor text:
1. In `interface JSONLMessage`, after `  version?: string;`, add:
```ts
  entrypoint?: string;
  toolUseResult?: unknown;
```
2. Immediately before `export async function parseConversation(`, add:
```ts
/**
 * AskUserQuestion answers: the user record's content is only a tool_result, and the
 * human's words live in toolUseResult.answers ({ "<question>": "<answer>" }).
 * Returns "Q: …\nA: …" pairs joined by a blank line, or '' for anything else.
 */
export function formatAnswers(toolUseResult: unknown): string {
  if (!toolUseResult || typeof toolUseResult !== 'object') return '';
  const answers = (toolUseResult as { answers?: unknown }).answers;
  if (!answers || typeof answers !== 'object' || Array.isArray(answers)) return '';
  return Object.entries(answers as Record<string, unknown>)
    .map(([question, answer]) => {
      const text = typeof answer === 'string' ? answer
        : Array.isArray(answer) ? answer.map(String).join(', ')
        : JSON.stringify(answer);
      return `Q: ${question}\nA: ${text}`;
    })
    .join('\n\n');
}

```
3. After `  let lineNumber = 0;` (the first occurrence, inside `parseConversation`), add `  let sessionEntrypoint: string | undefined;`.
4. In the `currentExchange` type, replace `    thinkingTriggers?: string;\n    toolCalls: ToolCall[];` with `    thinkingTriggers?: string;\n    isAnswer?: boolean;\n    toolCalls: ToolCall[];`.
5. In `finalizeExchange`, after `        thinkingTriggers: currentExchange.thinkingTriggers,`, add `        isAnswer: currentExchange.isAnswer,`.
6. After `      const parsed: JSONLMessage = JSON.parse(line);`, add:
```ts

      // The entrypoint (cli, sdk-py, sdk-cli) is per session; take the first one seen.
      if (!sessionEntrypoint && typeof parsed.entrypoint === 'string') {
        sessionEntrypoint = parsed.entrypoint;
      }
```
7. Immediately before `      // Skip empty messages`, add:
```ts
      // AskUserQuestion answers become the user side of a new exchange, so the
      // operator's decisions are searchable in their own words.
      const answerText = parsed.message.role === 'user' ? formatAnswers(parsed.toolUseResult) : '';
      if (answerText) {
        text = answerText;
      }

```
8. In the new-exchange object literal, after the line `          thinkingTriggers: parsed.thinkingMetadata?.triggers ? JSON.stringify(parsed.thinkingMetadata.triggers) : undefined,`, add `          isAnswer: answerText ? true : undefined,`.
9. Replace
```ts
  // Finalize last exchange
  finalizeExchange();
```
with
```ts
  // Finalize last exchange
  finalizeExchange();

  if (sessionEntrypoint) {
    for (const exchange of exchanges) exchange.entrypoint = sessionEntrypoint;
  }
```

**Step 5: Edit `codex-parser.ts`.**
1. After `  let cliVersion: string | undefined;`, add `  let originator: string | undefined;`.
2. After `      cliVersion = p.cli_version ?? cliVersion;`, add `      originator = p.originator ?? originator;`.
3. Replace the two lines `  finalize();\n  return exchanges;` at the end of `parseCodexConversation` with:
```ts
  finalize();
  // Codex provenance: session_meta.payload.originator ("Codex Desktop" is interactive).
  if (originator) {
    for (const exchange of exchanges) exchange.entrypoint = originator;
  }
  return exchanges;
```
Codex has no verified AskUserQuestion-equivalent answer record (see Open questions), so Q→A capture stays Claude-only.

**Step 6: Run the tests and see them pass.**
```bash
/Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory/node_modules/.bin/vitest run --root /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory src/parser-voice.test.ts src/codex-parser.test.ts src/archive-readers.test.ts
```
Expected: exit 0, `Tests  10 passed (10)`.

**Step 7: Type-check.**
```bash
/Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory/node_modules/.bin/tsc --noEmit -p /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory/tsconfig.json
```
Expected: no output, exit 0.

**Step 8: Stage.**
```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/mcp-servers/episodic-memory/src/parser.ts worker/mcp-servers/episodic-memory/src/codex-parser.ts worker/mcp-servers/episodic-memory/src/__fixtures__/claude-askuser-session.jsonl worker/mcp-servers/episodic-memory/src/parser-voice.test.ts
```
Expected: no output, exit 0.

---

## Task 6: Search returns provenance, excludes machine rows, and can prefer operator sessions (L2, L9)

**Files:**
- Modify: `worker/mcp-servers/episodic-memory/src/search.ts`. Edit `SearchOptions`, `searchConversations` and `formatResults`.
- Modify: `worker/mcp-servers/episodic-memory/src/mcp-server.ts`. Edit the zod schema (48-85), the JSON schema (141-168) and the search handler (200-256).
- Test: `worker/mcp-servers/episodic-memory/src/search-provenance.test.ts`

**Step 1: Write the test (RED).** Create `worker/mcp-servers/episodic-memory/src/search-provenance.test.ts`:
```ts
import { describe, it, expect, beforeAll, afterAll, vi } from 'vitest';
import fs from 'fs';
import os from 'os';
import path from 'path';

// The query embedding is the unit vector e0, so distances below are exact.
vi.mock('./embeddings.js', () => ({
  initEmbeddings: async () => {},
  generateEmbedding: async () => Array.from({ length: 384 }, (_, i) => (i === 0 ? 1 : 0)),
  generateExchangeEmbedding: async () => Array.from({ length: 384 }, (_, i) => (i === 0 ? 1 : 0)),
}));

let tmp: string;
const VEC = new Array(384).fill(0.01);
const E0 = Array.from({ length: 384 }, (_, i) => (i === 0 ? 1 : 0));
const E1 = Array.from({ length: 384 }, (_, i) => (i === 1 ? 1 : 0));

beforeAll(async () => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'search-prov-'));
  process.env.EPISODIC_MEMORY_CONFIG_DIR = path.join(tmp, 'config');
  process.env.TEST_DB_PATH = path.join(tmp, 'db.sqlite');
  process.env.TEST_ARCHIVE_DIR = path.join(tmp, 'archive');
  const { initDatabase, insertExchange, resetDatabaseCache } = await import('./db.js');
  resetDatabaseCache();
  const db = initDatabase();
  const row = (id: string, ts: string, text: string, entrypoint?: string, sessionClass?: string, vec: number[] = VEC) =>
    insertExchange(db, {
      id, project: '-Users-alice-Code-app', timestamp: ts, userMessage: text, assistantMessage: 'ok',
      archivePath: `/nonexistent/${id}.jsonl`, lineStart: 1, lineEnd: 2, entrypoint, sessionClass,
    }, vec);
  row('op1', '2026-10-01T00:00:00.000Z', 'KIWI_PROV operator decision', 'cli', 'operator');
  row('wk1', '2026-10-02T00:00:00.000Z', 'KIWI_PROV worker note', 'sdk-py', 'worker');
  row('mc1', '2026-10-03T00:00:00.000Z', 'KIWI_PROV grader chatter', undefined, 'machine');
  row('ao1', '2026-10-04T00:00:00.000Z', 'KIWI_PROV work-chunk pipeline', 'sdk-py', 'archive-only');
  row('legacy1', '2026-09-30T00:00:00.000Z', 'KIWI_PROV legacy row');
  // Twelve worker rows sit exactly on the query vector; one operator row is far away.
  for (let i = 1; i <= 12; i++) row(`vw${i}`, '2026-09-01T00:00:00.000Z', 'VECTOR_ONLY worker', 'sdk-py', 'worker', E0);
  row('vop', '2026-09-01T00:00:00.000Z', 'VECTOR_ONLY operator', 'cli', 'operator', E1);
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

describe('search provenance', () => {
  it('excludes machine and archive-only rows, keeps not-yet-classified rows, and returns provenance', async () => {
    const { searchConversations } = await import('./search.js');
    const hits = await searchConversations('KIWI_PROV', { mode: 'text' });
    expect(hits.map(h => h.exchange.id)).toEqual(['wk1', 'op1', 'legacy1']);
    const op = hits.find(h => h.exchange.id === 'op1')!;
    expect(op.exchange.sessionClass).toBe('operator');
    expect(op.exchange.entrypoint).toBe('cli');
  });

  it('sessionClass restricts results to one class', async () => {
    const { searchConversations } = await import('./search.js');
    const hits = await searchConversations('KIWI_PROV', { mode: 'text', sessionClass: 'operator' });
    expect(hits.map(h => h.exchange.id)).toEqual(['op1']);
  });

  it('preferOperator ranks operator sessions first without dropping others', async () => {
    const { searchConversations } = await import('./search.js');
    const hits = await searchConversations('KIWI_PROV', { mode: 'text', preferOperator: true });
    expect(hits.map(h => h.exchange.id)).toEqual(['op1', 'wk1', 'legacy1']);
  });

  it('formatResults shows the session class and entrypoint', async () => {
    const { searchConversations, formatResults } = await import('./search.js');
    const out = await formatResults(await searchConversations('KIWI_PROV', { mode: 'text', sessionClass: 'operator' }));
    expect(out).toContain('   Session: operator (cli)\n');
  });

  it('vector mode over-fetches when restricted to a class, so nearer rows of other classes do not crowd it out', async () => {
    const { searchConversations } = await import('./search.js');
    const hits = await searchConversations('anything', { mode: 'vector', limit: 3, sessionClass: 'operator' });
    expect(hits.map(h => h.exchange.id)).toEqual(['op1', 'vop']);
  });
});
```

**Step 2: Run the test and see it fail.**
```bash
/Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory/node_modules/.bin/vitest run --root /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory src/search-provenance.test.ts
```
Expected: FAIL, 5 failed.
- `mc1` is returned.
- No class filter is applied.
- No reordering happens.
- There is no `Session:` line.
- Vector mode returns three `vw*` rows.

**Step 3: Edit `search.ts` (GREEN).**
1. Replace `interface SearchOptions` (lines 8-13) with:
```ts
export type SessionClassFilter = 'operator' | 'brain' | 'worker' | 'unknown';

export interface SearchOptions {
  limit?: number;
  mode?: 'vector' | 'text' | 'both';
  after?: string;  // ISO date string
  before?: string; // ISO date string
  sessionClass?: SessionClassFilter;  // restrict to one provenance class
  preferOperator?: boolean;           // rank interactive (operator) sessions first
}
```
2. In `searchConversations`:
   - Change the destructuring to `const { limit = 10, mode = 'both', after, before, sessionClass, preferOperator } = options;`.
   - Replace the filter block from `// Build time filter clause with parameterized queries (defense-in-depth)` through `: '';` (the `timeClause` assignment) with:
```ts
  // Filters, parameterized (defense-in-depth). Machine-session rows never surface;
  // rows not yet classified (NULL, before the backfill) still do.
  const timeFilterClauses: string[] = [`(e.session_class IS NULL OR e.session_class NOT IN ('machine', 'archive-only'))`];
  const timeFilterParams: string[] = [];
  if (after) {
    timeFilterClauses.push('e.timestamp >= ?');
    timeFilterParams.push(after);
  }
  if (before) {
    // `before` is a date-only string (YYYY-MM-DD) but e.timestamp is a full ISO
    // datetime. A plain lexicographic `<= 'YYYY-MM-DD'` excludes the entire
    // boundary day. Bind the end-of-day instant so the whole `before` day is
    // included (mirrors `after`, which is inclusive via `>=`).
    timeFilterClauses.push('e.timestamp <= ?');
    timeFilterParams.push(`${before}T23:59:59.999Z`);
  }
  if (sessionClass) {
    timeFilterClauses.push('e.session_class = ?');
    timeFilterParams.push(sessionClass);
  }
  const timeClause = `AND ${timeFilterClauses.join(' AND ')}`;
```
   - In both SELECT lists, after `        e.line_end,`, add `        e.entrypoint,\n        e.session_class,`.
   - The vector query's filters run after the KNN picks `k` rows, so a class restriction could otherwise return almost nothing. In the vector branch:
     - replace the bound `limit,` argument of `stmt.all(` (the one after the embedding buffer) with `knnK,`;
     - add `const knnK = sessionClass ? Math.min(limit * 10, 500) : limit;` immediately before `const stmt = db.prepare(`;
     - add `results = results.slice(0, limit);` immediately after the `results = stmt.all(…);` statement.

     Date filters keep their existing post-filter behaviour, which is unchanged here.
   - Immediately before `  return results.map((row: any) => {`, add:
```ts
  if (preferOperator) {
    results = [
      ...results.filter((r: any) => r.session_class === 'operator'),
      ...results.filter((r: any) => r.session_class !== 'operator'),
    ];
  }

```
   - In the `exchange` object literal, after `      lineEnd: row.line_end`, add `,\n      entrypoint: row.entrypoint ?? undefined,\n      sessionClass: row.session_class ?? undefined`. Mind the comma after `row.line_end`.
3. In `formatResults`, after `    output += '\n';` (the line that ends the header), add:
```ts

    if (result.exchange.sessionClass) {
      output += `   Session: ${result.exchange.sessionClass}${result.exchange.entrypoint ? ` (${result.exchange.entrypoint})` : ''}\n`;
    }
```

**Step 4: Edit `mcp-server.ts`.**
1. In `SearchInputSchema`, after the `response_format: …` property (it ends with `),` before `})`), add:
```ts
    session_class: z
      .enum(['operator', 'brain', 'worker', 'unknown'])
      .optional()
      .describe('Only return exchanges from this kind of session: "operator" (interactive), "brain", "worker" (automation), "unknown" (no provenance recorded)'),
    prefer_operator: z
      .boolean()
      .optional()
      .describe('Rank exchanges from interactive (operator) sessions first'),
```
2. In the `search` tool's JSON `properties`, after `response_format: { type: 'string', enum: ['markdown', 'json'], default: 'markdown' },`, add:
```ts
            session_class: { type: 'string', enum: ['operator', 'brain', 'worker', 'unknown'] },
            prefer_operator: { type: 'boolean' },
```
3. In the multi-concept `options` object, after `before: params.before,`, add `sessionClass: params.session_class,\n          preferOperator: params.prefer_operator,`. Do the same in the single-concept `options: SearchOptions` object.

**Step 5: Run the tests and see them pass.**
```bash
/Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory/node_modules/.bin/vitest run --root /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory src/search-provenance.test.ts src/archive-readers.test.ts
```
Expected: exit 0, `Tests  9 passed (9)`.

**Step 6: Type-check.**
```bash
/Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory/node_modules/.bin/tsc --noEmit -p /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory/tsconfig.json
```
Expected: no output, exit 0.

**Step 7: Stage.**
```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/mcp-servers/episodic-memory/src/search.ts worker/mcp-servers/episodic-memory/src/mcp-server.ts worker/mcp-servers/episodic-memory/src/search-provenance.test.ts
```
Expected: no output, exit 0.

---

## Task 7: Sync — clone copies, machine exclusion, subagent archiving and provenance (L3, L9, L10)

**Files:**
- Modify: `worker/mcp-servers/episodic-memory/src/sync.ts`. Replace the whole file (220 lines).
- Modify: `worker/mcp-servers/episodic-memory/src/codex-sync.ts`. Replace the whole file (158 lines).
- Modify: `worker/mcp-servers/episodic-memory/src/indexer.ts`. Skip machine projects in the three project loops.
- Modify: `worker/mcp-servers/episodic-memory/src/codex-sync.test.ts:12-30`. Isolate the config dir.
- Test: `worker/mcp-servers/episodic-memory/src/sync-lean.test.ts`

**Step 1: Write the test (RED).** Create `worker/mcp-servers/episodic-memory/src/sync-lean.test.ts`:
```ts
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
```

**Step 2: Run the test and see it fail.**
```bash
/Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory/node_modules/.bin/vitest run --root /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory src/sync-lean.test.ts
```
Expected: FAIL, 3 failed (tests 1, 3 and 4). Test 4 fails because the current raw-file read misses the `.zst`-only copy, so it is summarized.
- `excluded` is undefined.
- The grader and `-private-tmp` sessions are archived.
- Subagents are not archived.
- The rows have a NULL class.

Test 2 passes before the change, because toggle-off behaviour equals today's behaviour. It is the negative case that pins the toggle once subagent archiving exists.

**Step 3: Replace `sync.ts` (GREEN)** with:
```ts
import fs from 'fs';
import path from 'path';
import { SUMMARIZER_CONTEXT_MARKER } from './constants.js';
import { getExcludedProjects } from './paths.js';
import { archiveNeedsCopy, copyIntoArchive, readArchiveText } from './archive-io.js';
import { loadSettings, isMachineProject, isArchiveOnlyProject, hasMachinePrefix, classifySession, readFirstUserText } from './settings.js';

const EXCLUSION_MARKERS = [
  '<INSTRUCTIONS-TO-EPISODIC-MEMORY>DO NOT INDEX THIS CHAT</INSTRUCTIONS-TO-EPISODIC-MEMORY>',
  'Only use NO_INSIGHTS_FOUND',
  SUMMARIZER_CONTEXT_MARKER,
];

function shouldSkipConversation(filePath: string): boolean {
  try {
    // Raw or compacted (.jsonl.zst): a compacted marked file must stay unsummarized.
    const content = readArchiveText(filePath);
    return EXCLUSION_MARKERS.some(marker => content.includes(marker));
  } catch (error) {
    // If we can't read the file, don't skip it
    return false;
  }
}

export interface SyncResult {
  copied: number;
  skipped: number;
  indexed: number;
  summarized: number;
  excluded: number;  // machine sessions: never archived, never indexed (L9)
  errors: Array<{ file: string; error: string }>;
}

export interface SyncOptions {
  skipIndex?: boolean;
  skipSummaries?: boolean;
  summaryLimit?: number; // Max summaries to generate per run (default: 10)
}

function extractSessionIdFromPath(filePath: string): string | null {
  // Extract session ID from filename: /path/to/abc-123-def.jsonl -> abc-123-def
  const basename = path.basename(filePath, '.jsonl');
  if (/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(basename)) {
    return basename;
  }
  return null;
}

/** Archive <project>/<session>/subagents/**.jsonl. Archived (and later compacted), never indexed (L10). */
function archiveSubagents(projectPath: string, destProjectDir: string, result: SyncResult): void {
  for (const entry of fs.readdirSync(projectPath, { withFileTypes: true })) {
    if (!entry.isDirectory()) continue;
    const subRoot = path.join(projectPath, entry.name, 'subagents');
    if (!fs.existsSync(subRoot)) continue;
    const stack = [subRoot];
    while (stack.length) {
      const dir = stack.pop()!;
      for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
        const full = path.join(dir, e.name);
        if (e.isDirectory()) { stack.push(full); continue; }
        if (!e.isFile() || !e.name.endsWith('.jsonl')) continue;
        try {
          if (copyIntoArchive(full, path.join(destProjectDir, path.relative(projectPath, full)))) result.copied++;
          else result.skipped++;
        } catch (error) {
          result.errors.push({ file: full, error: error instanceof Error ? error.message : String(error) });
        }
      }
    }
  }
}

export async function syncConversations(
  sourceDir: string,
  destDir: string,
  options: SyncOptions = {}
): Promise<SyncResult> {
  const result: SyncResult = { copied: 0, skipped: 0, indexed: 0, summarized: 0, excluded: 0, errors: [] };

  if (!fs.existsSync(sourceDir)) {
    return result;
  }

  const settings = loadSettings();
  const filesToIndex: string[] = [];
  const filesToSummarize: Array<{ path: string; sessionId: string }> = [];

  const projects = fs.readdirSync(sourceDir);
  const excludedProjects = getExcludedProjects();

  for (const project of projects) {
    if (excludedProjects.includes(project) || isMachineProject(project, settings)) {
      result.excluded++;
      continue;
    }

    const projectPath = path.join(sourceDir, project);
    const stat = fs.statSync(projectPath);
    if (!stat.isDirectory()) continue;

    const files = fs.readdirSync(projectPath).filter(f => f.endsWith('.jsonl'));
    // Archive-only projects (L16): archived and later compacted, never indexed or summarized.
    const archiveOnly = isArchiveOnlyProject(project, settings);

    for (const file of files) {
      const srcFile = path.join(projectPath, file);
      const destFile = path.join(destDir, project, file);

      try {
        if (archiveNeedsCopy(srcFile, destFile)) {
          // Probe and automated-review sessions are machine traffic: never archived.
          if (hasMachinePrefix(await readFirstUserText(srcFile), settings)) {
            result.excluded++;
            continue;
          }
          copyIntoArchive(srcFile, destFile);
          result.copied++;
          if (!archiveOnly) filesToIndex.push(destFile);
        } else {
          result.skipped++;
        }

        // Check if this file needs a summary (whether newly copied or existing)
        if (!options.skipSummaries && !archiveOnly) {
          const summaryPath = destFile.replace('.jsonl', '-summary.txt');
          if (!fs.existsSync(summaryPath) && !shouldSkipConversation(destFile)) {
            const sessionId = extractSessionIdFromPath(destFile);
            if (sessionId) {
              filesToSummarize.push({ path: destFile, sessionId });
            }
          }
        }
      } catch (error) {
        result.errors.push({
          file: srcFile,
          error: error instanceof Error ? error.message : String(error)
        });
      }
    }

    if (settings.exclude.archiveSubagents) {
      archiveSubagents(projectPath, path.join(destDir, project), result);
    }
  }

  // Index copied files (unless skipIndex is set)
  if (!options.skipIndex && filesToIndex.length > 0) {
    const { initDatabase, insertExchange } = await import('./db.js');
    const { initEmbeddings, generateExchangeEmbedding } = await import('./embeddings.js');
    const { parseConversation } = await import('./parser.js');

    const db = initDatabase();
    await initEmbeddings();

    for (const file of filesToIndex) {
      try {
        if (shouldSkipConversation(file)) {
          continue; // Skip indexing but file is already copied
        }

        const project = path.basename(path.dirname(file));
        const exchanges = await parseConversation(file, project, file);
        const sessionClass = classifySession({
          project,
          entrypoint: exchanges[0]?.entrypoint,
          firstUserText: exchanges[0]?.userMessage,
        }, settings);
        if (sessionClass === 'machine') {
          result.excluded++;
          continue;
        }
        if (sessionClass === 'archive-only') continue;

        for (const exchange of exchanges) {
          exchange.sessionClass = sessionClass;
          const toolNames = exchange.toolCalls?.map(tc => tc.toolName);
          const embedding = await generateExchangeEmbedding(
            exchange.userMessage,
            exchange.assistantMessage,
            toolNames
          );
          insertExchange(db, exchange, embedding, toolNames);
        }

        result.indexed++;
      } catch (error) {
        result.errors.push({
          file,
          error: error instanceof Error ? error.message : String(error)
        });
      }
    }

    db.close();
  }

  // Generate summaries for files that need them
  if (!options.skipSummaries && filesToSummarize.length > 0) {
    const { parseConversation } = await import('./parser.js');
    const { summarizeConversation } = await import('./summarizer.js');

    const summaryLimit = options.summaryLimit ?? 10;
    const toSummarize = filesToSummarize.slice(0, summaryLimit);
    const remaining = filesToSummarize.length - toSummarize.length;

    console.log(`Generating summaries for ${toSummarize.length} conversation(s)...`);
    if (remaining > 0) {
      console.log(`  (${remaining} more need summaries - will process on next sync)`);
    }

    for (const { path: filePath } of toSummarize) {
      try {
        const project = path.basename(path.dirname(filePath));
        const exchanges = await parseConversation(filePath, project, filePath);

        if (exchanges.length === 0) {
          continue; // Skip empty conversations
        }

        console.log(`  Summarizing ${path.basename(filePath)} (${exchanges.length} exchanges)...`);
        const summary = await summarizeConversation(exchanges);

        const summaryPath = filePath.replace('.jsonl', '-summary.txt');
        fs.writeFileSync(summaryPath, summary, 'utf-8');
        result.summarized++;
      } catch (error) {
        result.errors.push({
          file: filePath,
          error: `Summary generation failed: ${error instanceof Error ? error.message : String(error)}`
        });
      }
    }
  }

  return result;
}
```

**Step 4: Replace `codex-sync.ts`** with:
```ts
import fs from 'fs';
import path from 'path';
import { SyncResult, SyncOptions } from './sync.js';
import { SUMMARIZER_CONTEXT_MARKER } from './constants.js';
import { mungeCwd } from './codex-paths.js';
import { parseCodexConversation } from './codex-parser.js';
import { copyIntoArchive, readArchiveText } from './archive-io.js';
import { loadSettings, isMachineProject, isArchiveOnlyProject, classifySession } from './settings.js';

const EXCLUSION_MARKERS = [
  '<INSTRUCTIONS-TO-EPISODIC-MEMORY>DO NOT INDEX THIS CHAT</INSTRUCTIONS-TO-EPISODIC-MEMORY>',
  'Only use NO_INSIGHTS_FOUND',
  SUMMARIZER_CONTEXT_MARKER,
];

function shouldSkip(filePath: string): boolean {
  // Raw or compacted (.jsonl.zst).
  try { const text = readArchiveText(filePath); return EXCLUSION_MARKERS.some(m => text.includes(m)); }
  catch { return false; }
}

function walkRollouts(dir: string): string[] {
  const out: string[] = [];
  const stack = [dir];
  while (stack.length) {
    const d = stack.pop()!;
    let entries: fs.Dirent[];
    try { entries = fs.readdirSync(d, { withFileTypes: true }); } catch { continue; }
    for (const e of entries) {
      const full = path.join(d, e.name);
      if (e.isDirectory()) stack.push(full);
      else if (e.isFile() && /^rollout-.*\.jsonl$/.test(e.name)) out.push(full);
    }
  }
  return out;
}

// Reads the first line only, accumulating chunks until the first newline rather than
// a fixed buffer. A codex session_meta line embeds payload.base_instructions.text (the
// whole system prompt) and now routinely approaches 64KB; a truncated read made
// JSON.parse throw, which silently cost BOTH the project grouping (projectFor falls
// back to codex-<uuid>) and summarization (the meta.sessionId guard in
// syncCodexConversations). Chunks are concatenated and decoded ONCE so a multi-byte
// UTF-8 character split across a chunk boundary cannot corrupt the JSON.
export function readSessionMeta(file: string): { sessionId?: string; cwd?: string; originator?: string } {
  let fd: number | undefined;
  try {
    fd = fs.openSync(file, 'r');
    const CHUNK = 65536;
    const chunks: Buffer[] = [];
    const buf = Buffer.alloc(CHUNK);
    let position = 0;
    let newlineFound = false;

    while (!newlineFound) {
      const bytes = fs.readSync(fd, buf, 0, CHUNK, position);
      if (bytes === 0) break; // EOF before any newline — whole file is one line
      position += bytes;
      const read = buf.subarray(0, bytes);
      const nl = read.indexOf(0x0a);
      if (nl === -1) {
        chunks.push(Buffer.from(read));
      } else {
        chunks.push(Buffer.from(read.subarray(0, nl)));
        newlineFound = true;
      }
    }

    const firstLine = Buffer.concat(chunks).toString('utf-8');
    const rec = JSON.parse(firstLine);
    if (rec.type === 'session_meta' && rec.payload) {
      return { sessionId: rec.payload.session_id, cwd: rec.payload.cwd, originator: rec.payload.originator };
    }
  } catch { /* fall through */ }
  finally {
    if (fd !== undefined) { try { fs.closeSync(fd); } catch { /* already closed */ } }
  }
  return {};
}

function projectFor(file: string, cwd?: string): string {
  return cwd ? mungeCwd(cwd) : `codex-${path.basename(file, '.jsonl')}`;
}

export async function syncCodexConversations(
  sourceDir: string, destDir: string, options: SyncOptions = {}
): Promise<SyncResult> {
  const result: SyncResult = { copied: 0, skipped: 0, indexed: 0, summarized: 0, excluded: 0, errors: [] };
  if (!fs.existsSync(sourceDir)) return result;

  const settings = loadSettings();
  // The project is derived from the SOURCE session_meta and carried along, so a
  // compacted archive copy never has to be re-read for it.
  const filesToIndex: Array<{ path: string; project: string }> = [];
  const filesToSummarize: Array<{ path: string; project: string }> = [];

  for (const srcFile of walkRollouts(sourceDir)) {
    try {
      const meta = readSessionMeta(srcFile);
      const project = projectFor(srcFile, meta.cwd);
      if (isMachineProject(project, settings)) { result.excluded++; continue; }
      const archiveOnly = isArchiveOnlyProject(project, settings); // archived, never indexed/summarized (L16)
      const destFile = path.join(destDir, project, path.basename(srcFile));
      if (copyIntoArchive(srcFile, destFile)) { result.copied++; if (!archiveOnly) filesToIndex.push({ path: destFile, project }); }
      else result.skipped++;
      if (!options.skipSummaries && meta.sessionId && !archiveOnly) {
        const summaryPath = destFile.replace('.jsonl', '-summary.txt');
        if (!fs.existsSync(summaryPath) && !shouldSkip(destFile)) filesToSummarize.push({ path: destFile, project });
      }
    } catch (error) {
      result.errors.push({ file: srcFile, error: error instanceof Error ? error.message : String(error) });
    }
  }

  if (!options.skipIndex && filesToIndex.length > 0) {
    const { initDatabase, insertExchange } = await import('./db.js');
    const { initEmbeddings, generateExchangeEmbedding } = await import('./embeddings.js');
    const db = initDatabase();
    await initEmbeddings();
    for (const { path: file, project } of filesToIndex) {
      try {
        if (shouldSkip(file)) continue;
        const exchanges = await parseCodexConversation(file, project, file);
        const sessionClass = classifySession({ project, entrypoint: exchanges[0]?.entrypoint }, settings);
        if (sessionClass === 'machine') { result.excluded++; continue; }
        if (sessionClass === 'archive-only') continue;
        for (const ex of exchanges) {
          ex.sessionClass = sessionClass;
          const toolNames = ex.toolCalls?.map(tc => tc.toolName);
          const embedding = await generateExchangeEmbedding(ex.userMessage, ex.assistantMessage, toolNames);
          insertExchange(db, ex, embedding, toolNames);
        }
        result.indexed++;
      } catch (error) {
        result.errors.push({ file, error: error instanceof Error ? error.message : String(error) });
      }
    }
    db.close();
  }

  if (!options.skipSummaries && filesToSummarize.length > 0) {
    const { summarizeConversation } = await import('./summarizer.js');
    const limit = options.summaryLimit ?? 10;
    for (const { path: filePath, project } of filesToSummarize.slice(0, limit)) {
      try {
        const exchanges = await parseCodexConversation(filePath, project, filePath);
        if (exchanges.length === 0) continue;
        const summary = await summarizeConversation(exchanges);
        fs.writeFileSync(filePath.replace('.jsonl', '-summary.txt'), summary, 'utf-8');
        result.summarized++;
      } catch (error) {
        result.errors.push({ file: filePath, error: `Summary generation failed: ${error instanceof Error ? error.message : String(error)}` });
      }
    }
  }
  return result;
}
```

**Step 5: `indexer.ts`: skip machine and archive-only projects (L9, L16 "never indexed").** The legacy index CLI path skips these projects entirely; `sync` still archives the archive-only ones.
- Add `import { loadSettings, isMachineProject, isArchiveOnlyProject } from './settings.js';` after the archive-io import that Task 2 added.
- In each of `indexConversations`, `indexSession` and `indexUnprocessed`, add `const settings = loadSettings();` right after the existing `const excludedProjects = getExcludedProjects();` line.
- In the three project loops:
  - in `indexConversations`, replace `if (excludedProjects.includes(project)) {` with `if (excludedProjects.includes(project) || isMachineProject(project, settings) || isArchiveOnlyProject(project, settings)) {`;
  - in `indexSession` and `indexUnprocessed`, replace `if (excludedProjects.includes(project)) continue;` with `if (excludedProjects.includes(project) || isMachineProject(project, settings) || isArchiveOnlyProject(project, settings)) continue;`.

**Step 6: `codex-sync.test.ts`: isolate the config dir.** It previously read the real `~/.config/superpowers` through `getExcludedProjects`.
- In `beforeAll`, after `process.env.TEST_ARCHIVE_DIR = path.join(tmp, 'archive');`, add `  process.env.EPISODIC_MEMORY_CONFIG_DIR = path.join(tmp, 'config');`.
- In `afterAll`, after `delete process.env.TEST_ARCHIVE_DIR;`, add `  delete process.env.EPISODIC_MEMORY_CONFIG_DIR;`.

**Step 7: Run the tests and see them pass.**
```bash
/Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory/node_modules/.bin/vitest run --root /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory src/sync-lean.test.ts src/codex-sync.test.ts
```
Expected: exit 0, `Tests  9 passed (9)`. The existing `codex-sync` end-to-end test still loads the real embedding model and can take up to its 120 s timeout.

**Step 8: Type-check.**
```bash
/Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory/node_modules/.bin/tsc --noEmit -p /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory/tsconfig.json
```
Expected: no output, exit 0.

**Step 9: Stage.**
```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/mcp-servers/episodic-memory/src/sync.ts worker/mcp-servers/episodic-memory/src/codex-sync.ts worker/mcp-servers/episodic-memory/src/indexer.ts worker/mcp-servers/episodic-memory/src/codex-sync.test.ts worker/mcp-servers/episodic-memory/src/sync-lean.test.ts
```
Expected: no output, exit 0.

---

## Task 8: maintain.ts — the guarded one-time slim (L6, L9 D2)

**Files:**
- Create: `worker/mcp-servers/episodic-memory/src/maintain.ts`
- Create: `worker/mcp-servers/episodic-memory/src/__fixtures__/make-legacy-db.ts`
- Test: `worker/mcp-servers/episodic-memory/src/maintain-slim.test.ts`

**Step 1: Create the fixture builder.** Write `worker/mcp-servers/episodic-memory/src/__fixtures__/make-legacy-db.ts`:
```ts
/** Build an index in the pre-lean shape: old exchanges columns, tool_calls + its indexes, vec0. */
import Database from 'better-sqlite3';
import * as sqliteVec from 'sqlite-vec';

export const LEGACY_ROWS: Array<[string, string, string]> = [
  ['k1', '-Users-alice-Code-app', '2026-10-01T00:00:00.000Z'],
  ['k2', '-Users-alice-Code-app', '2026-10-02T00:00:00.000Z'],
  ['k3', '-Users-alice--ironclaude-brain', '2026-10-03T00:00:00.000Z'],
  ['k4', '-Users-alice-Code-app', '2026-10-04T00:00:00.000Z'],
  ['m1', '-Users-alice--ironclaude-grader', '2026-10-05T00:00:00.000Z'],
  ['m2', '-private-tmp', '2026-10-06T00:00:00.000Z'],
  ['w1', '-Users-alice-Code-rp-knowledge-base-programmatically-control-claude-work-chunk-CHUNK-1', '2026-10-07T00:00:00.000Z'],
];

export function legacyVector(index: number): Buffer {
  const v = new Float32Array(384);
  v[index] = 1;
  v[383] = 0.5;
  return Buffer.from(v.buffer);
}

export function makeLegacyDb(file: string, archiveRoot = '/archive'): void {
  const db = new Database(file);
  sqliteVec.load(db);
  db.pragma('journal_mode = WAL');
  db.exec(`CREATE TABLE exchanges (id TEXT PRIMARY KEY, project TEXT NOT NULL, timestamp TEXT NOT NULL, user_message TEXT NOT NULL, assistant_message TEXT NOT NULL, archive_path TEXT NOT NULL, line_start INTEGER NOT NULL, line_end INTEGER NOT NULL, embedding BLOB, last_indexed INTEGER, parent_uuid TEXT, is_sidechain BOOLEAN DEFAULT 0, session_id TEXT, cwd TEXT, git_branch TEXT, claude_version TEXT, thinking_level TEXT, thinking_disabled BOOLEAN, thinking_triggers TEXT)`);
  db.exec(`CREATE TABLE tool_calls (id TEXT PRIMARY KEY, exchange_id TEXT NOT NULL, tool_name TEXT NOT NULL, tool_input TEXT, tool_result TEXT, is_error BOOLEAN DEFAULT 0, timestamp TEXT NOT NULL, FOREIGN KEY (exchange_id) REFERENCES exchanges(id))`);
  db.exec(`CREATE INDEX idx_tool_name ON tool_calls(tool_name)`);
  db.exec(`CREATE INDEX idx_tool_exchange ON tool_calls(exchange_id)`);
  db.exec(`CREATE VIRTUAL TABLE vec_exchanges USING vec0(id TEXT PRIMARY KEY, embedding FLOAT[384])`);
  const ins = db.prepare(`INSERT INTO exchanges (id, project, timestamp, user_message, assistant_message, archive_path, line_start, line_end) VALUES (?, ?, ?, ?, ?, ?, 1, 2)`);
  const vec = db.prepare(`INSERT INTO vec_exchanges (id, embedding) VALUES (?, ?)`);
  const tool = db.prepare(`INSERT INTO tool_calls VALUES (?, ?, 'Write', ?, NULL, 0, ?)`);
  LEGACY_ROWS.forEach(([id, project, ts], i) => {
    ins.run(id, project, ts, `user ${id}`, `assistant ${id}`, `${archiveRoot}/${project}/${id}.jsonl`);
    vec.run(id, legacyVector(i));
    for (let t = 0; t < 20; t++) tool.run(`${id}-t${t}`, id, 'x'.repeat(4000), ts);
  });
  db.close();
}
```

**Step 2: Write the test (RED).** Create `worker/mcp-servers/episodic-memory/src/maintain-slim.test.ts`:
```ts
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import fs from 'fs';
import os from 'os';
import path from 'path';
import Database from 'better-sqlite3';
import * as sqliteVec from 'sqlite-vec';
import { makeLegacyDb, legacyVector } from './__fixtures__/make-legacy-db.js';

const BIG = () => 1e15;
const TINY = () => 1;
let tmp: string;
let dbPath: string;

function query(file: string, sql: string, ...params: unknown[]): any[] {
  const db = new Database(file, { readonly: true });
  sqliteVec.load(db);
  try { return db.prepare(sql).all(...params); } finally { db.close(); }
}
const tables = (file: string) => query(file, `SELECT name FROM sqlite_master WHERE type = 'table'`).map(r => r.name);

beforeEach(async () => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'maintain-slim-'));
  dbPath = path.join(tmp, 'db.sqlite');
  process.env.EPISODIC_MEMORY_CONFIG_DIR = path.join(tmp, 'config');
  process.env.TEST_DB_PATH = dbPath;
  process.env.TEST_ARCHIVE_DIR = path.join(tmp, 'archive');
  makeLegacyDb(dbPath);
  (await import('./db.js')).resetDatabaseCache();
});

afterEach(async () => {
  (await import('./db.js')).resetDatabaseCache();
  fs.rmSync(tmp, { recursive: true, force: true });
  delete process.env.EPISODIC_MEMORY_CONFIG_DIR;
  delete process.env.TEST_DB_PATH;
  delete process.env.TEST_ARCHIVE_DIR;
});

describe('slimIndex', () => {
  it('refuses when free disk is below twice the estimate, and changes nothing', async () => {
    const { slimIndex } = await import('./maintain.js');
    const r = await slimIndex({ dbPath, freeBytes: TINY });
    expect(r.action).toBe('refused');
    expect(r.detail).toMatch(/reclaimable \d+ bytes/);
    expect(tables(dbPath)).toContain('tool_calls');
    expect(fs.existsSync(dbPath + '.new')).toBe(false);
    expect(fs.existsSync(dbPath + '.bak')).toBe(false);
  });

  it('dry-run reports the estimate and changes nothing', async () => {
    const { slimIndex } = await import('./maintain.js');
    const r = await slimIndex({ dbPath, freeBytes: BIG, dryRun: true });
    expect(r.action).toBe('would-run');
    expect(r.detail).toMatch(/estimated slim size \d+ bytes/);
    expect(tables(dbPath)).toContain('tool_calls');
    expect(tables(dbPath)).not.toContain('meta');
  });

  it('slims: drops tool_calls, machine and archive-only rows, keeps vectors, swaps atomically, keeps .bak', async () => {
    const { slimIndex } = await import('./maintain.js');
    const r = await slimIndex({ dbPath, freeBytes: BIG, invokedBy: 'cli' });
    expect(r.action).toBe('done');
    expect(tables(dbPath)).not.toContain('tool_calls');
    expect(query(dbPath, `SELECT id, session_class FROM exchanges ORDER BY id`)).toEqual([
      { id: 'k1', session_class: 'unknown' },
      { id: 'k2', session_class: 'unknown' },
      { id: 'k3', session_class: 'brain' },
      { id: 'k4', session_class: 'unknown' },
    ]);
    expect(query(dbPath, `SELECT COUNT(*) AS c FROM vec_exchanges`)).toEqual([{ c: 4 }]);
    expect(query(dbPath, `SELECT id FROM vec_exchanges WHERE embedding MATCH ? AND k = 1`, legacyVector(1))).toEqual([{ id: 'k2' }]);
    expect(query(dbPath, `SELECT value FROM meta WHERE key = 'slim_done'`)).toEqual([{ value: '1' }]);
    expect(query(dbPath, `SELECT name FROM sqlite_master WHERE type = 'index' AND name IN ('idx_archive_path', 'idx_session_class') ORDER BY name`))
      .toEqual([{ name: 'idx_archive_path' }, { name: 'idx_session_class' }]);
    expect(fs.existsSync(dbPath + '.bak')).toBe(true);
    expect(tables(dbPath + '.bak')).toContain('tool_calls');
    expect(fs.existsSync(dbPath + '.new')).toBe(false);
    expect(fs.statSync(dbPath).size).toBeLessThan(fs.statSync(dbPath + '.bak').size);
  });

  it('is idempotent; .bak survives the slim run, --keep-bak and dry runs, and a later verified run (auto included) removes it', async () => {
    const { slimIndex } = await import('./maintain.js');
    await slimIndex({ dbPath, freeBytes: BIG, invokedBy: 'sync' });
    expect(fs.existsSync(dbPath + '.bak')).toBe(true);           // the slim run itself never deletes it
    const kept = await slimIndex({ dbPath, invokedBy: 'cli', keepBackup: true });
    expect(kept.action).toBe('skipped');
    expect(kept.detail).toContain('kept db.sqlite.bak');
    expect(fs.existsSync(dbPath + '.bak')).toBe(true);
    await slimIndex({ dbPath, invokedBy: 'sync', dryRun: true });
    expect(fs.existsSync(dbPath + '.bak')).toBe(true);
    const auto = await slimIndex({ dbPath, invokedBy: 'sync' });
    expect(auto.detail).toContain('removed db.sqlite.bak');
    expect(fs.existsSync(dbPath + '.bak')).toBe(false);
  });

  it('keeps .bak when re-verification of the slim index fails', async () => {
    const { slimIndex } = await import('./maintain.js');
    await slimIndex({ dbPath, freeBytes: BIG, invokedBy: 'sync' });
    const db = new Database(dbPath);
    db.prepare(`UPDATE meta SET value = '999' WHERE key = 'slim_rows'`).run(); // fewer rows than recorded
    db.close();
    const r = await slimIndex({ dbPath, invokedBy: 'sync' });
    expect(r.detail).toContain('verification of the current index failed');
    expect(fs.existsSync(dbPath + '.bak')).toBe(true);
  });

  it('finishes the swap after a crash between the two renames', async () => {
    const { slimIndex } = await import('./maintain.js');
    await expect(slimIndex({ dbPath, freeBytes: BIG, failpoint: 'after-first-rename' })).rejects.toThrow('failpoint after-first-rename');
    expect(fs.existsSync(dbPath)).toBe(false);
    expect(fs.existsSync(dbPath + '.bak')).toBe(true);
    expect(fs.existsSync(dbPath + '.new')).toBe(true);
    const r = await slimIndex({ dbPath, freeBytes: BIG });
    expect(r.action).toBe('done');
    expect(r.detail).toMatch(/resumed/);
    expect(tables(dbPath)).not.toContain('tool_calls');
    expect(fs.existsSync(dbPath + '.new')).toBe(false);
  });

  it('leaves the old index untouched after a crash before the swap, then rebuilds cleanly', async () => {
    const { slimIndex } = await import('./maintain.js');
    await expect(slimIndex({ dbPath, freeBytes: BIG, failpoint: 'after-copy' })).rejects.toThrow('failpoint after-copy');
    expect(tables(dbPath)).toContain('tool_calls');
    expect(fs.existsSync(dbPath + '.new')).toBe(true);
    const r = await slimIndex({ dbPath, freeBytes: BIG });
    expect(r.action).toBe('done');
    expect(fs.existsSync(dbPath + '.new')).toBe(false);
    expect(tables(dbPath)).not.toContain('tool_calls');
  });

  it('drops a tool_calls table that an older bundle recreated after the slim', async () => {
    const { slimIndex } = await import('./maintain.js');
    await slimIndex({ dbPath, freeBytes: BIG });
    const db = new Database(dbPath);
    db.exec(`CREATE TABLE tool_calls (id TEXT PRIMARY KEY, exchange_id TEXT NOT NULL, tool_name TEXT NOT NULL, tool_input TEXT, tool_result TEXT, is_error BOOLEAN DEFAULT 0, timestamp TEXT NOT NULL)`);
    db.close();
    const r = await slimIndex({ dbPath, freeBytes: BIG });
    expect(r.detail).toMatch(/dropped a recreated tool_calls table/);
    expect(tables(dbPath)).not.toContain('tool_calls');
  });

  it('purges machine rows in place when the index never had tool_calls (new install)', async () => {
    fs.rmSync(dbPath, { force: true });
    fs.rmSync(dbPath + '-wal', { force: true });
    fs.rmSync(dbPath + '-shm', { force: true });
    const { initDatabase, insertExchange, resetDatabaseCache } = await import('./db.js');
    const db = initDatabase();
    const base = { timestamp: '2026-10-01T00:00:00.000Z', userMessage: 'u', assistantMessage: 'a', lineStart: 1, lineEnd: 2 };
    insertExchange(db, { ...base, id: 'a1', project: '-Users-alice-Code-app', archivePath: '/a/a1.jsonl' }, new Array(384).fill(0.01));
    insertExchange(db, { ...base, id: 'g1', project: '-Users-alice--ironclaude-grader', archivePath: '/a/g1.jsonl' }, new Array(384).fill(0.01));
    resetDatabaseCache();
    const { slimIndex } = await import('./maintain.js');
    const r = await slimIndex({ dbPath });
    expect(r.action).toBe('done');
    expect(r.detail).toMatch(/removed 1 machine-session rows/);
    expect(query(dbPath, `SELECT id FROM exchanges`)).toEqual([{ id: 'a1' }]);
    expect((await slimIndex({ dbPath })).action).toBe('skipped');
  });
});
```

**Step 3: Run the test and see it fail.**
```bash
/Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory/node_modules/.bin/vitest run --root /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory src/maintain-slim.test.ts
```
Expected: FAIL, 9 failed. `./maintain.js` does not exist.

**Step 4: Implement (GREEN).** Create `worker/mcp-servers/episodic-memory/src/maintain.ts`:
```ts
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
import { applySchema, getMeta, setMeta, tableExists, resetDatabaseCache } from './db.js';
import { getDbPath } from './paths.js';
import { loadSettings, classifySession, EpisodicSettings } from './settings.js';

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

    const estimate = estimateSlimBytes(db);
    const free = (opts.freeBytes ?? defaultFreeBytes)(path.dirname(dbPath));
    const reclaimable = Math.max(0, fs.statSync(dbPath).size - estimate);
    if (free < 2 * estimate) {
      return report('slim', 'refused', `free disk ${free} bytes is below 2 x estimated slim size ${estimate} bytes; reclaimable ${reclaimable} bytes`);
    }
    if (opts.dryRun) {
      return report('slim', 'would-run', `estimated slim size ${estimate} bytes; free ${free} bytes; reclaimable ${reclaimable} bytes`);
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
```
If the ATTACH `SELECT … FROM old.vec_exchanges` fails at runtime with a sqlite-vec error, stop and report the exact error. Do not switch to copying shadow tables: `vector_chunks00` holds dead slots (5.39 GB measured, against about 0.38 GB of live vectors).

**Step 5: Run the test and see it pass.** Run the Step 3 command.
Expected: exit 0, `Tests  9 passed (9)`.

**Step 6: Type-check.**
```bash
/Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory/node_modules/.bin/tsc --noEmit -p /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory/tsconfig.json
```
Expected: no output, exit 0.

**Step 7: Stage.**
```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/mcp-servers/episodic-memory/src/maintain.ts worker/mcp-servers/episodic-memory/src/__fixtures__/make-legacy-db.ts worker/mcp-servers/episodic-memory/src/maintain-slim.test.ts
```
Expected: no output, exit 0.

---

## Task 9: maintain.ts — lossless compaction and the backup mirror (L7, L10, L11)

**Files:**
- Modify: `worker/mcp-servers/episodic-memory/src/maintain.ts`. Add imports and append the compaction and mirror code.
- Test: `worker/mcp-servers/episodic-memory/src/maintain-compact.test.ts`

**Step 1: Write the test (RED).** Create `worker/mcp-servers/episodic-memory/src/maintain-compact.test.ts`:
```ts
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import fs from 'fs';
import os from 'os';
import path from 'path';
import crypto from 'crypto';
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
```

**Step 2: Run the test and see it fail.**
```bash
/Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory/node_modules/.bin/vitest run --root /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory src/maintain-compact.test.ts
```
Expected: FAIL, 12 failed. `compactArchive` and `mirrorBackup` are not functions.

**Step 3: Add imports (GREEN).** In `maintain.ts`:
- Replace `import { getDbPath } from './paths.js';` with `import { getArchiveDir, getDbPath } from './paths.js';`.
- Add after the `import path from 'path';` line:
```ts
import crypto from 'crypto';
import zlib from 'zlib';
import { pipeline } from 'stream/promises';
import { ZST_SUFFIX, zstdAvailable } from './archive-io.js';
```

**Step 4: Append to the end of `maintain.ts`:**
```ts

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

/** Every transcript .jsonl under the archive (nested subagents included), never root files or temp copies. */
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
      if (!e.isFile() || !e.name.endsWith('.jsonl') || e.name.includes('.tmp.')) continue;
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
    fs.rmSync(file); // crash after the manifest line: finish the removal
    return 'resumed';
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
```

**Step 5: Run the tests and see them pass.**
```bash
/Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory/node_modules/.bin/vitest run --root /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory src/maintain-compact.test.ts src/maintain-slim.test.ts
```
Expected: exit 0, `Tests  21 passed (21)`.

**Step 6: Type-check.**
```bash
/Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory/node_modules/.bin/tsc --noEmit -p /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory/tsconfig.json
```
Expected: no output, exit 0.

**Step 7: Stage.**
```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/mcp-servers/episodic-memory/src/maintain.ts worker/mcp-servers/episodic-memory/src/maintain-compact.test.ts
```
Expected: no output, exit 0.

---

## Task 10: maintain.ts — voice backfill, the phase runner and the auto-run gate (L5, L9, L12, L15)

**Files:**
- Modify: `worker/mcp-servers/episodic-memory/src/maintain.ts`. Add imports and append the code below.
- Test: `worker/mcp-servers/episodic-memory/src/maintain-run.test.ts`

**Step 1: Write the test (RED).** Create `worker/mcp-servers/episodic-memory/src/maintain-run.test.ts`:
```ts
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import fs from 'fs';
import os from 'os';
import path from 'path';
import crypto from 'crypto';
import Database from 'better-sqlite3';
import * as sqliteVec from 'sqlite-vec';
import { fileURLToPath } from 'url';
import { makeLegacyDb } from './__fixtures__/make-legacy-db.js';
import { DEFAULT_SETTINGS } from './settings.js';

vi.mock('./embeddings.js', () => ({
  initEmbeddings: async () => {},
  generateEmbedding: async () => new Array(384).fill(0.01),
  generateExchangeEmbedding: async () => new Array(384).fill(0.01),
}));

const here = path.dirname(fileURLToPath(import.meta.url));
const ASK_FIXTURE = path.join(here, '__fixtures__', 'claude-askuser-session.jsonl');
const VEC = new Array(384).fill(0.01);
let tmp: string;
let dbPath: string;
let archive: string;

function query(sql: string, ...params: unknown[]): any[] {
  const db = new Database(dbPath, { readonly: true });
  sqliteVec.load(db);
  try { return db.prepare(sql).all(...params); } finally { db.close(); }
}

beforeEach(async () => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'maintain-run-'));
  dbPath = path.join(tmp, 'db.sqlite');
  archive = path.join(tmp, 'archive');
  process.env.EPISODIC_MEMORY_CONFIG_DIR = path.join(tmp, 'config');
  process.env.TEST_DB_PATH = dbPath;
  process.env.TEST_ARCHIVE_DIR = archive;
  (await import('./db.js')).resetDatabaseCache();
});
afterEach(async () => {
  (await import('./db.js')).resetDatabaseCache();
  fs.rmSync(tmp, { recursive: true, force: true });
  delete process.env.EPISODIC_MEMORY_CONFIG_DIR;
  delete process.env.TEST_DB_PATH;
  delete process.env.TEST_ARCHIVE_DIR;
});

async function seedPreLeanRows(): Promise<{ askPath: string; probePath: string; oldId: string }> {
  const app = path.join(archive, '-Users-alice-Code-app');
  fs.mkdirSync(app, { recursive: true });
  const askPath = path.join(app, '22222222-2222-4222-8222-222222222222.jsonl');
  fs.copyFileSync(ASK_FIXTURE, askPath);
  const probePath = path.join(app, '77777777-7777-4777-8777-777777777777.jsonl');
  fs.writeFileSync(probePath, [
    JSON.stringify({ entrypoint: 'sdk-cli', type: 'user', message: { role: 'user', content: 'Output only your exact model id string and nothing else.' }, timestamp: '2026-10-01T00:00:00.000Z' }),
    JSON.stringify({ entrypoint: 'sdk-cli', type: 'assistant', message: { role: 'assistant', content: [{ type: 'text', text: 'claude-x' }] }, timestamp: '2026-10-01T00:00:01.000Z' }),
  ].join('\n') + '\n');
  // Rows as the pre-lean parser wrote them: the answer was skipped, so one exchange spans lines 2-5.
  const oldId = crypto.createHash('md5').update(`${askPath}:2-5`).digest('hex');
  const { initDatabase, insertExchange, resetDatabaseCache } = await import('./db.js');
  const db = initDatabase();
  const base = { project: '-Users-alice-Code-app', timestamp: '2026-10-01T00:00:04.000Z' };
  insertExchange(db, { ...base, id: oldId, userMessage: 'Plan the KIWI migration', assistantMessage: 'I need one decision first.\n\nProceeding with retrieval only.', archivePath: askPath, lineStart: 2, lineEnd: 5 }, VEC);
  insertExchange(db, { ...base, id: 'probe-row', userMessage: 'Output only your exact model id string and nothing else.', assistantMessage: 'claude-x', archivePath: probePath, lineStart: 1, lineEnd: 2 }, VEC);
  db.exec(`DROP INDEX idx_archive_path`); // an existing index never got it at startup
  resetDatabaseCache();
  return { askPath, probePath, oldId };
}

describe('backfillVoice', () => {
  it('re-splits sessions with answers into Q→A exchanges, stamps provenance, removes machine sessions, once', async () => {
    const { askPath, oldId } = await seedPreLeanRows();
    const { backfillVoice } = await import('./maintain.js');
    const r = await backfillVoice({});
    expect(r.action).toBe('done');
    const rows = query(`SELECT id, user_message, entrypoint, session_class FROM exchanges ORDER BY line_start`);
    expect(rows.map(x => x.user_message)).toEqual(['Plan the KIWI migration', 'Q: Which scope?\nA: Retrieval only']);
    expect(rows.every(x => x.entrypoint === 'cli' && x.session_class === 'operator')).toBe(true);
    expect(rows.map(x => x.id)).not.toContain(oldId);
    expect(query(`SELECT COUNT(*) AS c FROM vec_exchanges`)).toEqual([{ c: 2 }]);
    expect(query(`SELECT value FROM meta WHERE key = 'voice_backfill_done'`)).toEqual([{ value: '1' }]);
    expect(query(`SELECT name FROM sqlite_master WHERE type = 'index' AND name = 'idx_archive_path'`)).toEqual([{ name: 'idx_archive_path' }]);
    expect(fs.existsSync(askPath)).toBe(true);
    expect((await backfillVoice({})).action).toBe('skipped');
  });

  it('dry-run changes nothing', async () => {
    await seedPreLeanRows();
    const { backfillVoice } = await import('./maintain.js');
    const r = await backfillVoice({ dryRun: true });
    expect(r.action).toBe('would-run');
    expect(r.detail).toContain('2 sessions');
    expect(query(`SELECT COUNT(*) AS c FROM exchanges`)).toEqual([{ c: 2 }]);
    expect(query(`SELECT COUNT(*) AS c FROM exchanges WHERE session_class IS NOT NULL`)).toEqual([{ c: 0 }]);
  });

  it('a later slim run still verifies and removes .bak after the backfill removed machine rows', async () => {
    makeLegacyDb(dbPath, archive);
    const k1 = path.join(archive, '-Users-alice-Code-app', 'k1.jsonl');
    fs.mkdirSync(path.dirname(k1), { recursive: true });
    fs.writeFileSync(k1, [
      JSON.stringify({ entrypoint: 'sdk-cli', type: 'user', message: { role: 'user', content: 'Output only your exact model id string and nothing else.' }, timestamp: '2026-10-01T00:00:00.000Z' }),
      JSON.stringify({ entrypoint: 'sdk-cli', type: 'assistant', message: { role: 'assistant', content: [{ type: 'text', text: 'claude-x' }] }, timestamp: '2026-10-01T00:00:01.000Z' }),
    ].join('\n') + '\n');
    const { slimIndex, backfillVoice } = await import('./maintain.js');
    expect((await slimIndex({ dbPath, freeBytes: () => 1e15, invokedBy: 'sync' })).action).toBe('done');
    expect(query(`SELECT value FROM meta WHERE key = 'slim_rows'`)).toEqual([{ value: '4' }]);
    const b = await backfillVoice({});
    expect(b.detail).toContain('machine sessions removed from the index 1');
    expect(query(`SELECT COUNT(*) AS c FROM exchanges`)).toEqual([{ c: 3 }]);
    const again = await slimIndex({ dbPath, invokedBy: 'sync' });
    expect(again.detail).toContain('removed db.sqlite.bak');
    expect(fs.existsSync(dbPath + '.bak')).toBe(false);
  });
});

describe('runMaintain and autoRunAllowed', () => {
  it('--only runs one phase', async () => {
    const { runMaintain } = await import('./maintain.js');
    const reports = await runMaintain({ only: 'compact', archiveDir: archive });
    expect(reports.map(r => r.phase)).toEqual(['compact']);
  });

  it('a dry-run over a legacy index writes nothing', async () => {
    makeLegacyDb(dbPath);
    const { runMaintain } = await import('./maintain.js');
    const reports = await runMaintain({ dryRun: true, archiveDir: archive, freeBytes: () => 1e15 });
    expect(reports.map(r => [r.phase, r.action])).toEqual([
      ['slim', 'would-run'], ['backfill', 'would-run'], ['compact', 'skipped'], ['mirror', 'skipped'],
    ]);
    const tables = query(`SELECT name FROM sqlite_master WHERE type = 'table'`).map(r => r.name);
    expect(tables).toContain('tool_calls');
    expect(tables).not.toContain('meta');
    expect(query(`SELECT name FROM pragma_table_info('exchanges') WHERE name = 'session_class'`)).toEqual([]);
  });

  it('an automatic run on a legacy index with too little disk refuses the slim and leaves the index untouched', async () => {
    makeLegacyDb(dbPath);
    const { runMaintain } = await import('./maintain.js');
    const reports = await runMaintain({ invokedBy: 'sync', archiveDir: archive, freeBytes: () => 1 });
    expect(reports.map(r => [r.phase, r.action])).toEqual([
      ['slim', 'refused'], ['backfill', 'skipped'], ['compact', 'skipped'], ['mirror', 'skipped'],
    ]);
    const tables = query(`SELECT name FROM sqlite_master WHERE type = 'table'`).map(r => r.name);
    expect(tables).toContain('tool_calls');
    expect(tables).not.toContain('meta');
    expect(fs.existsSync(dbPath + '.new')).toBe(false);
  });

  it('autoRunAllowed: on by default for every install, legacy-bloated included (L16); autoRun false opts out', async () => {
    const { autoRunAllowed } = await import('./maintain.js');
    const off = { ...DEFAULT_SETTINGS, maintain: { ...DEFAULT_SETTINGS.maintain, autoRun: false } };
    expect(autoRunAllowed(DEFAULT_SETTINGS)).toEqual({ allowed: true, reason: 'maintain.autoRun is true' });
    makeLegacyDb(dbPath);
    expect(autoRunAllowed(DEFAULT_SETTINGS)).toEqual({ allowed: true, reason: 'maintain.autoRun is true' });
    expect(autoRunAllowed(off)).toEqual({ allowed: false, reason: 'maintain.autoRun is false' });
    fs.mkdirSync(path.join(tmp, 'config'), { recursive: true });
    fs.writeFileSync(path.join(tmp, 'config', 'episodic-memory.json'), JSON.stringify({ maintain: { autoRun: false } }));
    const { loadSettings } = await import('./settings.js');
    expect(autoRunAllowed(loadSettings()).allowed).toBe(false);   // the operator's opt-out file
  });
});
```

**Step 2: Run the test and see it fail.**
```bash
/Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory/node_modules/.bin/vitest run --root /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory src/maintain-run.test.ts
```
Expected: FAIL, 7 failed. `backfillVoice`, `runMaintain` and `autoRunAllowed` are not functions.

**Step 3: Add imports (GREEN).** In `maintain.ts`:
- Replace `import { applySchema, getMeta, setMeta, tableExists, resetDatabaseCache } from './db.js';` with `import { applySchema, getMeta, setMeta, tableExists, resetDatabaseCache, initDatabase, insertExchange, deleteExchange, ensureProvenanceIndexes } from './db.js';`.
- Replace `import { ZST_SUFFIX, zstdAvailable } from './archive-io.js';` with `import { ZST_SUFFIX, zstdAvailable, archiveExists } from './archive-io.js';`.
- Add:
```ts
import { parseConversation } from './parser.js';
import { parseCodexConversation } from './codex-parser.js';
```

**Step 4: Append to the end of `maintain.ts`:**
```ts

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
  const counts = { sessions: 0, answers: 0, replaced: 0, machine: 0, missing: 0 };

  for (const { p, project } of sessions) {
    if (!archiveExists(p)) {
      counts.missing++;
      setMeta(db, 'voice_backfill_cursor', p);
      continue;
    }
    const parsed = path.basename(p).startsWith('rollout-')
      ? await parseCodexConversation(p, project, p)
      : await parseConversation(p, project, p);
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
    `machine sessions removed from the index ${counts.machine}; missing archive files ${counts.missing}`);
}

// ---------------------------------------------------------------------------
// runner + automatic-run gate (L5, L15)
// ---------------------------------------------------------------------------

export async function runMaintain(opts: MaintainOptions & { only?: Phase } = {}): Promise<PhaseReport[]> {
  const settings = opts.settings ?? loadSettings();
  const o: MaintainOptions = { ...opts, settings };
  const run: Record<Phase, () => Promise<PhaseReport>> = {
    slim: () => slimIndex(o),
    backfill: () => backfillVoice(o),
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
```

**Step 5: Run the tests and see them pass.**
```bash
/Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory/node_modules/.bin/vitest run --root /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory src/maintain-run.test.ts src/maintain-slim.test.ts src/maintain-compact.test.ts
```
Expected: exit 0, `Tests  28 passed (28)`.

**Step 6: Type-check.**
```bash
/Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory/node_modules/.bin/tsc --noEmit -p /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory/tsconfig.json
```
Expected: no output, exit 0.

**Step 7: Stage.**
```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/mcp-servers/episodic-memory/src/maintain.ts worker/mcp-servers/episodic-memory/src/maintain-run.test.ts
```
Expected: no output, exit 0.

---

## Task 11: One lock and a runtime budget; maintain CLI; sync-cli wiring; bundle (L4, L5)

**Files:**
- Create: `worker/mcp-servers/episodic-memory/src/sync-lock.ts`
- Create: `worker/mcp-servers/episodic-memory/src/maintain-cli.ts`
- Modify: `worker/mcp-servers/episodic-memory/src/sync-cli.ts`. Replace the whole file (100 lines).
- Modify: `worker/mcp-servers/episodic-memory/package.json:15`. Edit the `bundle` script.
- Modify: `worker/mcp-servers/episodic-memory/cli/episodic-memory.js:39-46,87-93`
- Modify: `worker/mcp-servers/episodic-memory/cli/mcp-server-wrapper.js:58,62,98-112`
- Test: `worker/mcp-servers/episodic-memory/src/sync-lock.test.ts`
- Test: `worker/mcp-servers/episodic-memory/src/maintain-cli.test.ts`

**Step 1: Write the tests (RED).**

Create `worker/mcp-servers/episodic-memory/src/sync-lock.test.ts`:
```ts
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import fs from 'fs';
import os from 'os';
import path from 'path';
import { spawnSync } from 'child_process';

let tmp: string;
let lp: string;
beforeEach(() => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'sync-lock-'));
  lp = path.join(tmp, 'sync.lock');
  process.env.EPISODIC_MEMORY_CONFIG_DIR = path.join(tmp, 'config');
});
afterEach(() => {
  fs.rmSync(tmp, { recursive: true, force: true });
  delete process.env.EPISODIC_MEMORY_CONFIG_DIR;
});
const holder = (h: object) => fs.writeFileSync(lp, JSON.stringify(h));

describe('sync lock', () => {
  it('acquires when free, refuses while a live holder has it, and releases', async () => {
    const { acquireLock, releaseLock } = await import('./sync-lock.js');
    expect(acquireLock(lp, 60_000, 'sync').acquired).toBe(true);
    expect(JSON.parse(fs.readFileSync(lp, 'utf-8')).pid).toBe(process.pid);
    const second = acquireLock(lp, 60_000, 'sync');
    expect(second.acquired).toBe(false);
    expect(second.holder?.pid).toBe(process.pid);
    releaseLock(lp);
    expect(fs.existsSync(lp)).toBe(false);
  });

  it('reclaims a lock whose holder PID is dead', async () => {
    const { acquireLock } = await import('./sync-lock.js');
    const dead = spawnSync(process.execPath, ['-e', '']).pid;
    holder({ pid: dead, startedAt: Date.now(), kind: 'sync' });
    const r = acquireLock(lp, 60_000, 'sync');
    expect(r).toEqual({ acquired: true, reclaimed: true });
  });

  it('reclaims a sync lock older than maxAge even if its PID is alive', async () => {
    const { acquireLock } = await import('./sync-lock.js');
    holder({ pid: process.ppid, startedAt: Date.now() - 10_000, kind: 'sync' });
    expect(acquireLock(lp, 5_000, 'sync')).toEqual({ acquired: true, reclaimed: true });
  });

  it('never reclaims a live manual (cli) run by age', async () => {
    const { acquireLock } = await import('./sync-lock.js');
    holder({ pid: process.ppid, startedAt: Date.now() - 10_000, kind: 'cli' });
    expect(acquireLock(lp, 5_000, 'sync').acquired).toBe(false);
  });

  it('releaseLock leaves a lock owned by another PID', async () => {
    const { releaseLock } = await import('./sync-lock.js');
    holder({ pid: process.ppid, startedAt: Date.now(), kind: 'sync' });
    releaseLock(lp);
    expect(fs.existsSync(lp)).toBe(true);
  });

  it('getLockPath lives in the index dir under the config dir', async () => {
    const { getLockPath } = await import('./sync-lock.js');
    expect(getLockPath()).toBe(path.join(tmp, 'config', 'conversation-index', 'sync.lock'));
  });

  it('startWatchdog fires after its budget and does not keep the process alive', async () => {
    const { startWatchdog } = await import('./sync-lock.js');
    await new Promise<void>(resolve => { startWatchdog(20, resolve); });
    const t = startWatchdog(60_000, () => {});
    expect(t.hasRef()).toBe(false);
    clearTimeout(t);
  });
});
```

Create `worker/mcp-servers/episodic-memory/src/maintain-cli.test.ts`:
```ts
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import fs from 'fs';
import os from 'os';
import path from 'path';
import { spawnSync } from 'child_process';
import { fileURLToPath } from 'url';

const here = path.dirname(fileURLToPath(import.meta.url));
const pkgRoot = path.resolve(here, '..');
let tmp: string;

function env() {
  return {
    EPISODIC_MEMORY_CONFIG_DIR: path.join(tmp, 'config'),
    TEST_DB_PATH: path.join(tmp, 'db.sqlite'),
    TEST_ARCHIVE_DIR: path.join(tmp, 'archive'),
  };
}
beforeEach(() => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'maintain-cli-'));
  Object.assign(process.env, env());
});
afterEach(() => {
  vi.restoreAllMocks();
  fs.rmSync(tmp, { recursive: true, force: true });
  delete process.env.EPISODIC_MEMORY_CONFIG_DIR;
  delete process.env.TEST_DB_PATH;
  delete process.env.TEST_ARCHIVE_DIR;
});

describe('maintain CLI', () => {
  it('rejects unknown options with exit code 2', async () => {
    const { main } = await import('./maintain-cli.js');
    expect(await main(['--bogus'])).toBe(2);
  });

  it('exits 3 while another live process holds the lock, and leaves that lock alone', async () => {
    const { main } = await import('./maintain-cli.js');
    const { getLockPath } = await import('./sync-lock.js');
    fs.writeFileSync(getLockPath(), JSON.stringify({ pid: process.ppid, startedAt: Date.now(), kind: 'sync' }));
    expect(await main(['--dry-run'])).toBe(3);
    expect(JSON.parse(fs.readFileSync(getLockPath(), 'utf-8')).pid).toBe(process.ppid);
  });

  it('a dry-run runs every phase and releases the lock', async () => {
    const { main } = await import('./maintain-cli.js');
    const { getLockPath } = await import('./sync-lock.js');
    const log = vi.spyOn(console, 'log').mockImplementation(() => {});
    expect(await main(['--dry-run'])).toBe(0);
    const lines = log.mock.calls.map(c => String(c[0]));
    expect(lines.map(l => l.split(':')[0])).toEqual(['slim', 'backfill', 'compact', 'mirror']);
    expect(fs.existsSync(getLockPath())).toBe(false);
  });

  it('--only=compact runs only compaction', async () => {
    const { main } = await import('./maintain-cli.js');
    const log = vi.spyOn(console, 'log').mockImplementation(() => {});
    expect(await main(['--only=compact', '--dry-run'])).toBe(0);
    expect(log.mock.calls.map(c => String(c[0]).split(':')[0])).toEqual(['compact']);
  });

  it('is wired into the bundle, the episodic-memory CLI and the auto-build wrapper', () => {
    const pkg = JSON.parse(fs.readFileSync(path.join(pkgRoot, 'package.json'), 'utf-8'));
    expect(pkg.scripts.bundle).toContain('esbuild src/maintain-cli.ts --bundle');
    expect(pkg.scripts.bundle).toContain('--outfile=dist/maintain-cli.js');
    const cli = fs.readFileSync(path.join(pkgRoot, 'cli', 'episodic-memory.js'), 'utf-8');
    expect(cli).toContain("case 'maintain':");
    expect(cli).toContain("join(distDir, 'maintain-cli.js')");
    const wrapper = fs.readFileSync(path.join(pkgRoot, 'cli', 'mcp-server-wrapper.js'), 'utf-8');
    expect(wrapper).toContain("join(EPISODIC_MEMORY_ROOT, 'dist', 'maintain-cli.js')");
    expect(wrapper).toContain('--outfile=dist/maintain-cli.js');
  });
});

describe('sync-cli under the lock', () => {
  const runSyncCli = () => spawnSync(process.execPath, ['--import', 'tsx', path.join(pkgRoot, 'src', 'sync-cli.ts')], {
    cwd: pkgRoot, encoding: 'utf-8', timeout: 60_000,
    env: { ...process.env, ...env(), HOME: path.join(tmp, 'home') },
  });

  it('skips the whole run while a live process holds the lock', async () => {
    const { getLockPath } = await import('./sync-lock.js');
    fs.writeFileSync(getLockPath(), JSON.stringify({ pid: process.ppid, startedAt: Date.now(), kind: 'cli' }));
    const r = runSyncCli();
    expect(r.status).toBe(0);
    expect(r.stdout).toContain('holds');
    expect(r.stdout).not.toContain('Syncing conversations');
  }, 60_000);

  it('runs sync then maintain on an empty new install, exits 0 and releases the lock', async () => {
    const { getLockPath } = await import('./sync-lock.js');
    fs.mkdirSync(path.join(tmp, 'home'), { recursive: true });
    const r = runSyncCli();
    expect(r.status).toBe(0);
    expect(r.stdout).toContain('Syncing conversations');
    expect(r.stdout).toContain('Running maintain');
    expect(fs.existsSync(getLockPath())).toBe(false);
  }, 60_000);
});
```

**Step 2: Run the tests and see them fail.**
```bash
/Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory/node_modules/.bin/vitest run --root /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory src/sync-lock.test.ts src/maintain-cli.test.ts
```
Expected: FAIL. `sync-lock.js` and `maintain-cli.js` do not exist. `sync-cli` neither prints "holds" nor "Running maintain". The wiring strings are absent.

**Step 3: Create `src/sync-lock.ts` (GREEN):**
```ts
/**
 * sync-lock.ts — one Node-side lock for the sync worker and the maintain CLI.
 *
 * The hook's mkdir lock (worker/hooks/episodic-memory-sync.sh) is released as soon as
 * `sync-cli --background` forks its detached child, and a manual CLI run never goes
 * through the hook. This lock is held by the process that actually does the work.
 * A sync lock is stale when its PID is dead or it is older than maxAgeMs (the sync
 * worker also has a runtime budget, see startWatchdog). A manual (cli) lock is
 * stale only when its PID is dead: a long manual maintain run is never stolen.
 */
import fs from 'fs';
import path from 'path';
import { getIndexDir } from './paths.js';

export type LockKind = 'sync' | 'cli';
export interface LockHolder { pid: number; startedAt: number; kind?: LockKind; }

export function getLockPath(): string {
  return path.join(getIndexDir(), 'sync.lock');
}

export function pidAlive(pid: number): boolean {
  try { process.kill(pid, 0); return true; }
  catch (error: any) { return error?.code === 'EPERM'; }
}

export function acquireLock(
  lockPath: string, maxAgeMs: number, kind: LockKind, now = Date.now()
): { acquired: boolean; holder?: LockHolder; reclaimed?: boolean } {
  fs.mkdirSync(path.dirname(lockPath), { recursive: true });
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      const fd = fs.openSync(lockPath, 'wx');
      try { fs.writeSync(fd, JSON.stringify({ pid: process.pid, startedAt: now, kind })); }
      finally { fs.closeSync(fd); }
      return attempt === 0 ? { acquired: true } : { acquired: true, reclaimed: true };
    } catch (error: any) {
      if (error?.code !== 'EEXIST') throw error;
      let holder: LockHolder | undefined;
      try { holder = JSON.parse(fs.readFileSync(lockPath, 'utf-8')); } catch { holder = undefined; }
      const stale = !holder || !pidAlive(holder.pid)
        || (holder.kind !== 'cli' && now - holder.startedAt > maxAgeMs);
      if (!stale) return { acquired: false, holder };
      fs.rmSync(lockPath, { force: true });
    }
  }
  return { acquired: false };
}

export function releaseLock(lockPath: string): void {
  try {
    const holder = JSON.parse(fs.readFileSync(lockPath, 'utf-8')) as LockHolder;
    if (holder.pid === process.pid) fs.rmSync(lockPath, { force: true });
  } catch { /* already gone */ }
}

/** Run `onExpire` after `ms`; unref'd, so it never keeps the process alive by itself. */
export function startWatchdog(ms: number, onExpire: () => void): NodeJS.Timeout {
  const timer = setTimeout(onExpire, ms);
  timer.unref();
  return timer;
}
```

**Step 4: Create `src/maintain-cli.ts`:**
```ts
import fs from 'fs';
import { pathToFileURL } from 'url';
import { runMaintain, PHASES, Phase } from './maintain.js';
import { acquireLock, releaseLock, getLockPath } from './sync-lock.js';
import { loadSettings } from './settings.js';

export const USAGE = `Usage: episodic-memory maintain [--dry-run] [--only=slim|backfill|compact|mirror] [--keep-bak]

Reclaims episodic-memory disk space without losing information:
  slim      one-time rebuild of a legacy index without the unused tool_calls table and
            machine-session rows (needs free disk >= 2x the slim size; keeps db.sqlite.bak)
  backfill  one-time indexing of AskUserQuestion answers and session provenance
  compact   compress archived transcripts that are no longer live to .jsonl.zst
            (sha256-verified; restore any file with: zstd -d <file>.jsonl.zst)
  mirror    copy verified .zst files to backup.mirrorDir, if configured

OPTIONS:
  --dry-run       Report what each phase would do; change nothing
  --only=PHASE    Run one phase
  --keep-bak      Keep db.sqlite.bak (otherwise a manual run removes it once the slim index verifies)
`;

export async function main(argv: string[]): Promise<number> {
  if (argv.includes('--help') || argv.includes('-h')) { console.log(USAGE); return 0; }
  let only: Phase | undefined;
  for (const arg of argv) {
    if (arg === '--dry-run' || arg === '--keep-bak') continue;
    const phase = arg.startsWith('--only=') ? arg.slice('--only='.length) : '';
    if (phase && (PHASES as string[]).includes(phase)) { only = phase as Phase; continue; }
    console.error(`Unknown option: ${arg}\n\n${USAGE}`);
    return 2;
  }
  const settings = loadSettings();
  const lockPath = getLockPath();
  const lock = acquireLock(lockPath, settings.maintain.maxRuntimeMinutes * 2 * 60_000, 'cli');
  if (!lock.acquired) {
    console.log(`Another episodic-memory sync or maintain run holds ${lockPath} (PID ${lock.holder?.pid ?? 'unknown'}); try again later.`);
    return 3;
  }
  try {
    const reports = await runMaintain({
      dryRun: argv.includes('--dry-run'), keepBackup: argv.includes('--keep-bak'), only, invokedBy: 'cli', settings,
    });
    for (const r of reports) console.log(`${r.phase}: ${r.action} — ${r.detail}`);
    return reports.some(r => r.action === 'error') ? 1 : 0;
  } finally {
    releaseLock(lockPath);
  }
}

const entry = process.argv[1];
if (entry && fs.existsSync(entry) && import.meta.url === pathToFileURL(fs.realpathSync(entry)).href) {
  main(process.argv.slice(2)).then(code => process.exit(code), error => { console.error(error); process.exit(1); });
}
```

**Step 5: Replace `src/sync-cli.ts`** with:
```ts
import { syncConversations } from './sync.js';
import { getArchiveDir, getIndexDir } from './paths.js';
import fs from 'fs';
import path from 'path';
import os from 'os';
import { spawn } from 'child_process';
import { syncCodexConversations } from './codex-sync.js';
import { loadSettings } from './settings.js';
import { acquireLock, releaseLock, getLockPath, startWatchdog } from './sync-lock.js';
import { runMaintain, autoRunAllowed } from './maintain.js';

const args = process.argv.slice(2);

if (args.includes('--help') || args.includes('-h')) {
  console.log(`
Usage: episodic-memory sync [--background]

Sync conversations from ~/.claude/projects and ~/.codex/sessions to the archive and index them.

This command:
1. Copies new or updated .jsonl files to the conversation archive (machine sessions excluded)
2. Generates embeddings for semantic search
3. Updates the search index
4. Runs \`episodic-memory maintain\` when maintain.autoRun allows it

Only processes files that are new or have been modified since last sync.
Safe to run multiple times - subsequent runs are fast no-ops. One run at a time:
a second run while one holds the lock exits immediately.

OPTIONS:
  --background    Run sync in background (for hooks, returns immediately)

EXAMPLES:
  # Sync all new conversations
  episodic-memory sync

  # Sync in background (for hooks)
  episodic-memory sync --background
`);
  process.exit(0);
}

// If background mode, fork the process and exit immediately. The detached child's
// output goes to <index dir>/sync.log (rotated to sync.log.1 above 5 MB), so a
// background sync or maintain run leaves evidence.
if (args.includes('--background')) {
  const filteredArgs = args.filter(arg => arg !== '--background');
  const logPath = path.join(getIndexDir(), 'sync.log');
  try {
    if (fs.statSync(logPath).size > 5 * 1024 * 1024) fs.renameSync(logPath, `${logPath}.1`);
  } catch { /* no log yet */ }
  const out = fs.openSync(logPath, 'a');
  const child = spawn(process.execPath, [process.argv[1], ...filteredArgs], { detached: true, stdio: ['ignore', out, out] });
  fs.closeSync(out);
  child.unref();
  console.log(`Sync started in background (log: ${logPath})...`);
  process.exit(0);
}

const settings = loadSettings();
const lockPath = getLockPath();
const maxRuntimeMs = settings.maintain.maxRuntimeMinutes * 60_000;
const lock = acquireLock(lockPath, maxRuntimeMs * 2, 'sync');
if (!lock.acquired) {
  console.log(`Another episodic-memory sync or maintain run holds ${lockPath} (PID ${lock.holder?.pid ?? 'unknown'}); skipping.`);
  process.exit(0);
}

// A hung sync must not block every later session's sync: bound this run.
startWatchdog(maxRuntimeMs, () => {
  console.error(`Sync exceeded ${settings.maintain.maxRuntimeMinutes} minutes; exiting so the next session can sync.`);
  releaseLock(lockPath);
  process.exit(124);
});

const sourceDir = path.join(os.homedir(), '.claude', 'projects');
const codexSourceDir = path.join(os.homedir(), '.codex', 'sessions');
const destDir = getArchiveDir();

async function run(): Promise<void> {
  console.log(`=== sync ${new Date().toISOString()} (PID ${process.pid}) ===`);
  console.log('Syncing conversations...');
  console.log(`Source: ${sourceDir}`);
  console.log(`Destination: ${destDir}\n`);

  const result = await syncConversations(sourceDir, destDir);
  console.log(`\n✅ Sync complete!`);
  console.log(`  Copied: ${result.copied}`);
  console.log(`  Skipped: ${result.skipped}`);
  console.log(`  Excluded (machine): ${result.excluded}`);
  console.log(`  Indexed: ${result.indexed}`);
  console.log(`  Summarized: ${result.summarized}`);
  if (result.errors.length > 0) {
    console.log(`\n⚠️  Errors: ${result.errors.length}`);
    result.errors.forEach(err => console.log(`  ${err.file}: ${err.error}`));
  }

  console.log('\nSyncing Codex conversations...');
  console.log(`Source: ${codexSourceDir}`);
  const codexResult = await syncCodexConversations(codexSourceDir, destDir);
  console.log(`\n✅ Codex sync complete!`);
  console.log(`  Copied: ${codexResult.copied}`);
  console.log(`  Skipped: ${codexResult.skipped}`);
  console.log(`  Excluded (machine): ${codexResult.excluded}`);
  console.log(`  Indexed: ${codexResult.indexed}`);
  console.log(`  Summarized: ${codexResult.summarized}`);
  if (codexResult.errors.length > 0) {
    console.log(`\n⚠️  Codex errors: ${codexResult.errors.length}`);
    codexResult.errors.forEach(err => console.log(`  ${err.file}: ${err.error}`));
  }

  const gate = autoRunAllowed(settings);
  if (!gate.allowed) {
    console.log(`\nMaintain: skipped (${gate.reason})`);
    return;
  }
  console.log('\nRunning maintain...');
  const reports = await runMaintain({ invokedBy: 'sync', settings });
  for (const r of reports) console.log(`  ${r.phase}: ${r.action} — ${r.detail}`);
}

run()
  .then(() => { releaseLock(lockPath); process.exit(0); })
  .catch(error => { console.error('Error syncing:', error); releaseLock(lockPath); process.exit(1); });
```

**Step 6: `package.json` line 15.** Append to the end of the `bundle` string, inside the closing quote:
```
 && esbuild src/maintain-cli.ts --bundle --platform=node --format=esm --outfile=dist/maintain-cli.js --external:fsevents --external:@anthropic-ai/claude-agent-sdk --external:sharp --external:onnxruntime-node --external:better-sqlite3 --external:@xenova/transformers --external:sqlite-vec
```

**Step 7: `cli/episodic-memory.js`.**
- In `showHelp`, after the line `  repair      Backfill indexing gaps in grown session files (run once after upgrade)`, add `  maintain    Reclaim disk space losslessly (slim index, compress archive, optional backup)`.
- After the repair example (`  episodic-memory repair\`);`), insert before the closing backtick:
```

  # See what maintain would reclaim, then run it
  episodic-memory maintain --dry-run
  episodic-memory maintain
```
  The help string must still end with a backtick followed by `);`.
- After the `case 'repair':` block (`await runScript(join(distDir, 'repair-cli.js'), args);\n        break;`), add:
```js

      case 'maintain':
        await runScript(join(distDir, 'maintain-cli.js'), args);
        break;
```

**Step 8: `cli/mcp-server-wrapper.js`.**
- After line 58 (`const syncCliFile = …`), add `  const maintainCliFile = join(EPISODIC_MEMORY_ROOT, 'dist', 'maintain-cli.js');`.
- On line 62, change `existsSync(syncCliFile) &&` to `existsSync(syncCliFile) && existsSync(maintainCliFile) &&`.
- After the `// Build dist/sync-cli.js bundle if missing` block (it ends at line 112), add:
```js

  // Build dist/maintain-cli.js bundle if missing
  if (!existsSync(maintainCliFile)) {
    log('[auto-build] Building dist/maintain-cli.js with esbuild...');
    try {
      execSync(
        'npx esbuild src/maintain-cli.ts --bundle --platform=node --format=esm --outfile=dist/maintain-cli.js --external:fsevents --external:@anthropic-ai/claude-agent-sdk --external:sharp --external:onnxruntime-node --external:better-sqlite3 --external:@xenova/transformers --external:sqlite-vec',
        { cwd: EPISODIC_MEMORY_ROOT, stdio: 'pipe', timeout: 60000 }
      );
      log('[auto-build] dist/maintain-cli.js built successfully');
    } catch (err) {
      log(`[auto-build] FAILED to build dist/maintain-cli.js: ${err.message}`);
      if (err.stderr) log(`[auto-build] stderr: ${err.stderr.toString().slice(0, 2000)}`);
      process.exit(1);
    }
  }
```

**Step 9: Run the tests and see them pass.** Run the Step 2 command.
Expected: exit 0, `Tests  14 passed (14)`.

**Step 10: Type-check the whole package.**
```bash
/Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory/node_modules/.bin/tsc --noEmit -p /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory/tsconfig.json
```
Expected: no output, exit 0.

**Step 10a: Confirm the operator's opt-out is still in place before building.** The build puts the automatic-run code into the repo's `dist/`, and a session launched from the repo would run it.
```bash
grep -c '"autoRun":false' /Users/roberthyatt/.config/superpowers/episodic-memory.json
```
Expected: `1`. Any other output, or a missing file, means **stop**: do not build, and report.

**Step 11: Build tsc output and all three bundles in place.** `dist/` is gitignored and is not staged. `parent-death-exit.test.ts` needs `dist/mcp-server.js`.
```bash
npm --prefix /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory run build
```
Expected: exit 0. The output includes esbuild lines naming `dist/mcp-server.js`, `dist/sync-cli.js` and `dist/maintain-cli.js`.

**Step 12: Smoke-test the bundled CLI against throwaway directories.** It must never touch the real `~/.config/superpowers`.
```bash
env EPISODIC_MEMORY_CONFIG_DIR=/tmp/ic-em-cli-check/config TEST_DB_PATH=/tmp/ic-em-cli-check/db.sqlite TEST_ARCHIVE_DIR=/tmp/ic-em-cli-check/archive node /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory/dist/maintain-cli.js --dry-run
```
Expected: exit 0, and exactly four lines:
```
slim: skipped — no index database yet
backfill: skipped — no index database yet
compact: skipped — nothing to compact
mirror: skipped — no backup.mirrorDir configured
```

**Step 13: Stage.**
```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/mcp-servers/episodic-memory/src/sync-lock.ts worker/mcp-servers/episodic-memory/src/maintain-cli.ts worker/mcp-servers/episodic-memory/src/sync-cli.ts worker/mcp-servers/episodic-memory/package.json worker/mcp-servers/episodic-memory/cli/episodic-memory.js worker/mcp-servers/episodic-memory/cli/mcp-server-wrapper.js worker/mcp-servers/episodic-memory/src/sync-lock.test.ts worker/mcp-servers/episodic-memory/src/maintain-cli.test.ts
```
Expected: no output, exit 0.

---

## Task 12: README episodic-memory section (L13)

No tests are required: this task is documentation only.

**Files:**
- Modify: `README.md`. Insert a section between `## Wiki Knowledge Layer` (it ends at line 584) and `## Configuration Reference` (line 588). Note: `README.md` already has staged changes from another effort; this task adds only the new section.

**Step 1: Insert the section.** Immediately before the `---` line that precedes `## Configuration Reference`, insert:
````markdown
---

## Episodic Memory

Episodic memory lets Claude and Codex search past conversations. Its data lives in `~/.config/superpowers/`. `$XDG_CONFIG_HOME/superpowers` or `EPISODIC_MEMORY_CONFIG_DIR` overrides that location; the legacy name is kept on purpose.

### What is stored

| Store | Path | Contents |
|---|---|---|
| Archive | `conversation-archive/<project>/` | Copies of Claude (`~/.claude/projects`) and Codex (`~/.codex/sessions`) transcripts, plus `<session>-summary.txt` summaries. Subagent transcripts go to `<project>/<session>/subagents/`; they are archived but not indexed. |
| Index | `conversation-index/db.sqlite` | One row per exchange: user and assistant text, AskUserQuestion answers as `Q: … / A: …` pairs, the session `entrypoint`, the `session_class`, and a 384-dimension vector. |
| Settings | `episodic-memory.json` | Optional. Every key has a default (see below). |

Each session gets a `session_class`. Search results show it, and the `search` tool's `session_class` and `prefer_operator` options use it. The class is derived from transcript metadata and never from anyone's name.

| Class | Meaning |
|---|---|
| `operator` | an interactive session (Claude `entrypoint: cli`; Codex originator `Codex Desktop` or `codex-tui`) |
| `brain` | the Commander Brain (`~/.ironclaude/brain`) |
| `worker` | automation: managed worker worktrees, `sdk-*` entrypoints, Codex `codex_exec` and `ironclaude` |
| `unknown` | older transcripts that record no entrypoint, or an entrypoint not listed in the settings |
| `archive-only` | archived and compressed, never indexed or summarized (default: knowledge-base work-chunk pipeline sessions) |
| `machine` | excluded; see below |

### What is excluded

Machine sessions are never archived or indexed. They are graders, judges, temp and scratch directories, model probes (first message "Output only your exact model id string and nothing else.") and automated security reviews (first message "Review this change for security vulnerabilities."). The default rules match project directory names (`--ironclaude-grader`, `claude-judge-`, `/tmp`, macOS `$TMPDIR`) and those first-message prefixes.

Archive-only projects (`archiveOnlyProjectPatterns`) and subagent transcripts (`archiveSubagents`) are kept in the archive but never indexed.

To change them, edit `episodic-memory.json`. An array you supply **replaces** the default array, so start from the defaults, which are shown here in full:

```json
{
  "maintain": { "autoRun": true, "liveWindowHours": 24, "zstdLevel": 3, "maxRuntimeMinutes": 120 },
  "exclude": {
    "machineProjectPatterns": ["--(ironclaude|tron)-grader$", "claude-judge-", "^-private-tmp($|-)", "^-tmp($|-)", "^-(private-)?var-folders-"],
    "machineFirstUserTextPrefixes": ["Output only your exact model id string and nothing else.", "Review this change for security vulnerabilities."],
    "archiveSubagents": true,
    "archiveOnlyProjectPatterns": ["-programmatically-control-claude-work-chunk-"]
  },
  "classify": {
    "brainProjectPatterns": ["--(ironclaude|tron)-brain$"],
    "workerProjectPatterns": ["--ironclaude-worktrees-"],
    "operatorEntrypoints": ["cli", "Codex Desktop", "codex-tui"],
    "automationEntrypoints": ["codex_exec", "ironclaude"],
    "automationEntrypointPrefixes": ["sdk-"]
  },
  "backup": { "mirrorDir": null }
}
```

The existing `conversation-index/exclude.txt` (exact project names) still works.

### Compressed archive, and how to restore an original

Once an archived transcript has not changed for `liveWindowHours` (24 by default), `maintain` compresses `<session>.jsonl` to `<session>.jsonl.zst`. It removes the original only after three checks pass: the compressed file is decompressed again, its SHA-256 matches the original, and a line is appended to `conversation-archive/manifest.jsonl`. Failures go to `manifest-errors.jsonl` and leave the original in place. Summaries stay uncompressed.

Search and `read` handle `.zst` files transparently. Reading them needs Node 22.15 or later, which has built-in zstd. To restore a byte-identical original:

```bash
zstd -d ~/.config/superpowers/conversation-archive/<project>/<session>.jsonl.zst
```

### Backup mirror (optional)

Set `"backup": { "mirrorDir": "/Volumes/Backup/episodic-memory" }` to copy every verified `.zst` there, with a checksum check on each copy. If the directory is unavailable, `maintain` logs a warning and continues. There is no remote backup.

### The `maintain` command

```bash
episodic-memory maintain --dry-run     # report what each phase would do; change nothing
episodic-memory maintain               # run all phases
episodic-memory maintain --only=compact
episodic-memory maintain --keep-bak    # keep db.sqlite.bak even after it verifies
```

The phases are idempotent; a finished phase is a cheap no-op on later runs.

- **`slim`.** A one-time rebuild of an index that still has the unused `tool_calls` table. It also drops machine-session and archive-only rows.
  - It refuses unless free disk is at least twice the new index's size.
  - It verifies row counts, the newest timestamp and a sample search before an atomic swap, and keeps the old index as `db.sqlite.bak`.
  - A **later** run, automatic or manual, deletes `db.sqlite.bak` after re-verifying the new index. `--keep-bak` and `--dry-run` keep it.
- **`backfill`.** A one-time pass that indexes past AskUserQuestion answers and session provenance from the archive. Where answers split an old exchange, it replaces the superseded index rows. It only reads the archive and never modifies it.
- **`compact`.** Compresses the archive, as described above.
- **`mirror`.** The optional backup mirror.

The background sync runs at every session start and takes the same lock. When `maintain.autoRun` is `true`, the default for every install including existing ones, it runs `maintain` automatically. The guards above still apply. Set it to `false` to opt out.

**Upgrading an existing install:** nothing is required.
- The first automatic run slims the index once enough disk is free, then backfills and compresses.
- The next run removes `db.sqlite.bak` after verifying.

To control it by hand instead:
1. Set `"maintain": {"autoRun": false}` before upgrading.
2. Run `episodic-memory maintain --dry-run`, then `episodic-memory maintain --keep-bak`.
3. Check search and `read`.
4. Set `autoRun` back to `true`.

**Notes:**
- An MCP server started before the slim keeps reading the old index until it restarts.
- After upgrading IronClaude, refresh **both** the Claude and Codex plugin caches; an old bundle recreates `tool_calls`, which the next `maintain` drops again.
- Do not run `episodic-memory index --rebuild` on such an install. It deletes the index and re-indexes only transcripts still under `~/.claude/projects`, losing everything that exists only in the archive.
````

**Step 2: Verify the section landed once, in the right place.**
```bash
grep -n -e "^## Wiki Knowledge Layer" -e "^## Episodic Memory$" -e "^## Configuration Reference" /Users/roberthyatt/Code/ironclaude/README.md
```
Expected: exactly three lines, in the order `## Wiki Knowledge Layer`, `## Episodic Memory`, `## Configuration Reference`.

**Step 3: Stage.**
```bash
git -C /Users/roberthyatt/Code/ironclaude add README.md
```
Expected: no output, exit 0.

---

## Task 13: Full verification

No new tests: this task only runs the existing suites. It changes no source file, since Task 11 already built `dist/`. The results are recorded in `docs/reviews/2026-10-07-episodic-memory-lean-verification.md`.

"Workspace" in the design's testing strategy is read as every suite this change can affect: episodic-memory, the hooks and Commander. No workspace-manager file changes, so its suite is not in scope.

**Files:**
- Create: `docs/reviews/2026-10-07-episodic-memory-lean-verification.md`

**Step 1: The full episodic-memory suite.**
```bash
/Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory/node_modules/.bin/vitest run --root /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory
```
Expected: exit 0, 0 failed. Record the actual `Test Files` and `Tests` lines. If the 19 pre-existing cases (statically counted) all still exist, the total is 117 (19 + 98 new). Paste the actual line into the task submission.

**Step 2: The hook suites.** Each suite's output goes to its own log, so a failure is visible and kept.
```bash
for s in /Users/roberthyatt/Code/ironclaude/worker/hooks/test-*.sh /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-*.sh /Users/roberthyatt/Code/ironclaude/commander/hooks/tests/test-*.sh; do bash "$s" > "/tmp/ic-em-hooks-$(basename "$s").log" 2>&1 || echo "FAIL $s"; done; echo DONE
```
Expected: the last line is `DONE`. Record every `FAIL` line. A failure whose log shows `rg: command not found` (or similar) is the pre-existing `rg` dependency failure. Any other failure is a regression: stop and report it. This plan changes no hook file.

**Step 3: The Commander pytest suite.**
```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q
```
Expected: record the actual summary line.
- **Known failure, not a regression:** `tests/test_executing_plans_skill.py::test_execution_complete_restage_command_stages_from_non_root_cwd`, measured 2026-10-06 as "1 failed, 3760 passed".
- **Any other failure:** stop and report it. This plan changes no Commander file.

**Step 4: Confirm the staged set for this loop.**
```bash
git -C /Users/roberthyatt/Code/ironclaude diff --cached --name-only -- worker/mcp-servers/episodic-memory README.md
```
Expected: the 37 paths below and no others. Nothing under `dist/` appears.
- **Source:**
  - `src/archive-io.ts`, `archive-io.test.ts`, `archive-readers.test.ts`;
  - `parser.ts`, `codex-parser.ts`, `search.ts`, `show-cli.ts`, `repair-cli.ts`, `mcp-server.ts`, `indexer.ts`, `verify.ts`;
  - `db.ts`, `types.ts`, `db.test.ts`;
  - `settings.ts`, `settings.test.ts`;
  - `__fixtures__/claude-askuser-session.jsonl`, `parser-voice.test.ts`;
  - `search-provenance.test.ts`;
  - `sync.ts`, `codex-sync.ts`, `codex-sync.test.ts`, `sync-lean.test.ts`;
  - `maintain.ts`, `__fixtures__/make-legacy-db.ts`, `maintain-slim.test.ts`, `maintain-compact.test.ts`, `maintain-run.test.ts`;
  - `sync-lock.ts`, `maintain-cli.ts`, `sync-cli.ts`, `sync-lock.test.ts`, `maintain-cli.test.ts`.
- **CLI:** `package.json`, `cli/episodic-memory.js`, `cli/mcp-server-wrapper.js`.
- **Docs:** `README.md`.

That is 37 entries: 33 under `worker/mcp-servers/episodic-memory/src`, 3 package/CLI files, and `README.md`.

**Step 5: Write the verification note.** Create `docs/reviews/2026-10-07-episodic-memory-lean-verification.md`. Record every command from Steps 1–4 with its actual result lines, every `FAIL` line classified as pre-existing or regression, and the result of the staged-set check.

**Step 6: Stage the verification note.**
```bash
git -C /Users/roberthyatt/Code/ironclaude add -f docs/reviews/2026-10-07-episodic-memory-lean-verification.md
```
Expected: no output, exit 0.

---

## After the loop (not a task; operator only, L15 steps 2–4, L16)

Claude and Codex currently load episodic memory from the plugin caches, so deploying means refreshing those caches. The first real run on the operator's box is manual and needs the operator's go. Nothing in this plan runs it. The only write under `~/.config` is the opt-out file from the execution prerequisite.

1. **Confirm the opt-out written at the prerequisite still reads back exactly.**
   ```bash
   cat /Users/roberthyatt/.config/superpowers/episodic-memory.json
   ```
   Expected output, exactly: `{"maintain":{"autoRun":false}}`.
2. **Deploy.** With an operator go, refresh **both** the Claude and Codex plugin caches. Each background sync appends to `/Users/roberthyatt/.config/superpowers/conversation-index/sync.log`, under a `=== sync <ISO> (PID n) ===` header. Confirm that log shows `Maintain: skipped (maintain.autoRun is false)`.
3. **Confirm no old sync is running.** An old-version `sync-cli.js` from a stale cache does not know the Node lock. If one writes during the slim's copy, its rows land in `db.sqlite.bak`.
   - `pgrep -fl episodic-memory/dist/sync-cli.js` must list no process from an old cache path. This is a listing only; never kill by pattern.
   - Syncs skip while the manual run holds the lock. On 44 GB this can take hours.
4. **Dry run.** Review the report:
   ```bash
   node /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory/dist/maintain-cli.js --dry-run
   ```
5. **Real run.** With the operator's go:
   ```bash
   node /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory/dist/maintain-cli.js --keep-bak
   ```
   Then verify search and `read` against compacted files. `db.sqlite.bak` stays.
6. **Turn the automatic run on, after the operator signs off.** Set `"autoRun": true`, or delete the file, since the default is on. The next automatic run re-verifies the slim index and only then removes `db.sqlite.bak`.
7. **Release.** Ship through `/commit`, with a separate explicit go for the release push. Other installs get the automatic run by default, guarded by the 2× disk check, the lock and verification.

## Decisions applied (L16)

- **Auto-run.** On for every install, including legacy-bloated ones. The operator's box is opted out by the after-loop step 1.
- **Security-review and model-probe sessions.** Machine by default. The markers are the verified first-message prefixes.
- **roleplaying-agents work-chunk sessions.** Archive-only by default, through the configurable `archiveOnlyProjectPatterns`.
- **Backfill.** It replaces superseded index rows. The archive is never modified by backfill.
- **`pkill -f` in `test-episodic-memory-sync-singleton.sh`.** Out of scope; it is queued separately.

## Open questions

1. **Workers that run in the primary checkout.** These cannot be told apart from operator sessions by path alone. They are classified by `entrypoint`; Commander workers measured as `sdk-py`, so they fall under `worker`. Brain sessions carry no entrypoint, but their path makes them `brain`. Two cases outside the Brain and worker paths get `unknown` rather than a guess:
   - old transcripts without an entrypoint;
   - any entrypoint not yet listed in the settings.

   The `session_class: operator` filter therefore excludes `unknown` rows. `prefer_operator` only reorders, so it keeps them.
2. **Codex Q→A answers.** No verified Codex answer-record shape was found. `request_user_input` appears in rollouts but was not confirmed as a user-answer record. Q→A capture is Claude-only; Codex gets provenance only.
3. **`dist/` is gitignored and untracked**, so it cannot be staged. Task 11 rebuilds `dist/` in place for the tests and the smoke check. Deployment is the cache refresh in the after-loop steps.
