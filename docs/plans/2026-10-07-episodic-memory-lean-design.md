# Episodic Memory: Lean and Useful (Design)

> **Created:** 2026-10-07
> **Status:** Design Complete
> **Requirements:** `docs/plans/2026-10-07-episodic-memory-lean-requirements.md` (L1–L14)
> **Loop:** full. It covers a DB schema and data migration, concurrency (the sync lock), file deletion behind verification, and a product feature that ships to all users. A final Fable review is required before commit.

## Summary

Episodic memory has grown to about 86 GB on the operator's machine. Its index is 42 GB, and 82% of that is a table nothing reads. Its archive is 44 GB of raw transcripts, and some of those transcripts are grader-on-grader machine sessions.

This design fixes both stores and keeps every piece of information:

- **At the source:** stop writing the dead table, and stop archiving machine sessions.
- **For the operator's voice:** index AskUserQuestion answers and session provenance, so the Commander can act as "what would <operator> do".
- **For existing stores:** reclaim space once, with a guarded, idempotent `maintain` routine. It ships with IronClaude, the singleton sync worker runs it automatically, and it is also available as a CLI.

## Architecture

```text
sync (singleton worker) ──► copy into archive (clone-on-APFS; skip excluded; include subagents/)
                         ──► index new exchanges (Q→A + entrypoint; no tool_calls)
                         ──► maintain()  [idempotent]
                               ├─ slimIndexIfLegacy()   guarded ATTACH-copy + atomic swap (+ .bak)
                               ├─ backfillVoice()       one-time Q→A + provenance from archive
                               ├─ compactArchive()      .jsonl → .jsonl.zst, sha256-gated, manifest
                               └─ mirrorBackup()        optional, fail-soft, checksummed
readers ──► archive-io helper (tries .jsonl then .jsonl.zst; nested paths)
```

Progress is recorded so each phase is skipped once it is complete:
- **State markers:** `meta` rows in the DB, for example `slim_done`, `voice_backfill_done` and `schema_version`.
- **The manifest:** `manifest.jsonl` in the archive, one entry per compacted file.

## Components

1. **`db.ts`**
   - Drop the CREATE, INSERT and INDEX statements for `tool_calls`.
   - Add migrations for the `entrypoint` and `session_class` columns (`operator` | `brain` | `worker` | `machine`, derived from entrypoint, path and exclude rules).
   - Add a small `meta` key/value table.
2. **Parsers (`parser.ts`, `codex-parser.ts`)**
   - When a user record has `toolUseResult.answers`, emit "Q: … / A: …" text as that exchange's `userMessage`.
   - Capture `entrypoint`.
   - Codex: capture the equivalent if present. If the source shows no equivalent, record that and keep this part Claude-only.
3. **`search.ts` and MCP search output.** Return `entrypoint` and `session_class`. Add an optional filter that prefers or restricts results to `operator` sessions. Machine rows are excluded.
4. **`archive-io.ts` (new).** Provides `openArchiveStream(path)`, `readArchiveText(path)` and `archiveExists(path)`. Each tries `.jsonl`, then `.jsonl.zst` (Node's built-in zstd). Every reader listed in L8 uses it.
5. **Sync paths (`sync.ts`, `codex-sync.ts`)**
   - Copy with `COPYFILE_FICLONE`.
   - Apply the exclude list before copying.
   - Walk `<session>/subagents/` recursively; those files are archived but marked not-to-index.
6. **Exclude config**
   - It is a JSON file in the episodic-memory config directory. Default rules match grader, judge and validator projects, the `/private/tmp` analyzer sessions, and a `subagents` archive toggle.
   - The exact rule format, and the default match criteria (`cwd`, the `-p` analyzer marker, project name), are set during planning from verified examples.
7. **`maintain.ts` (new)** and the CLI entry `episodic-memory maintain [--dry-run] [--only=slim|backfill|compact|mirror]`, bundled in `dist/`.
   - Every phase checks its marker and reports what it would do under `--dry-run`.
   - Every phase can resume after being interrupted.
8. **`sync-cli.ts`.** Call `maintain()` after sync, under the existing singleton lock. Add a staleness or timeout check to the guard.
9. **README.** Add an episodic-memory section covering storage, exclusions, the compressed archive and how to restore, the backup option, and the CLI.

## Data Flow: the one-time slim

1. Under the sync lock, estimate the slim size (`exchanges` plus vector bytes) and check free disk against twice that estimate. If there is not enough space, log the bytes that would be reclaimed and stop.
2. Create `db.sqlite.new` with the new schema, ATTACH the old database, and `INSERT … SELECT` non-machine `exchanges` rows plus the matching vector rows, copying the bytes exactly.
3. Verify row counts against the filtered source counts, `MAX(timestamp)`, and that a fixed sample query returns the same ids.
4. Rename `db.sqlite` to `db.sqlite.bak` and `db.sqlite.new` to `db.sqlite`, delete the old `-wal` and `-shm` files, and set `slim_done`.
5. On the next run, re-verify and delete the `.bak`.
6. Long-lived MCP readers that still hold the old inode see stale but valid data until they restart. Document this.

## Error Handling

- **A step fails:** stop, leave the old store untouched, and log. Nothing is deleted before verification passes.
- **Checksum mismatch during compaction:** delete the `.zst` and keep the raw file. Record the error in the manifest's error log.
- **Backup mirror unavailable:** warn and continue.
- **Lock held by a live owner:** skip `maintain` for this run.

## Testing Strategy

Tests use fixture databases and archives in temporary directories. Every behaviour has a positive and a negative case:
- **Slim and swap:** counts preserved; machine rows dropped; refuses when disk is low; idempotent on a second run; resumes after a crash between steps.
- **Readers:** read `.jsonl` and `.jsonl.zst`, including nested `subagents/` paths.
- **Compaction:**
  - a checksum mismatch keeps the raw file;
  - a live file is skipped;
  - the manifest is written;
  - restoring with `zstd -d` gives byte-identical output.
- **Sync:**
  - excluded sessions are neither archived nor indexed;
  - subagents are archived but not indexed;
  - clone copies fall back to a real copy where cloning is unsupported.
- **Parser:**
  - question/answer text is captured;
  - entrypoint is captured;
  - nothing is hard-coded to one operator.
- **Backfill:** idempotent.
- **Mirror:** fails soft.

Run the full workspace and episodic-memory suites, the hook suites, and the Commander pytest. A final Fable review comes before commit. The operator's first real run is manual: `maintain --dry-run`, then a real run with their go.

## Implementation Notes

- Planning must verify, from current source, every `file:line` cited here and in the requirements before the task list is written.
- Free disk at design time was about 29.5 GiB, and the slim index is about 7.7 GB, so the slim fits.
- Compaction needs at most one file's worth of extra space at a time.
