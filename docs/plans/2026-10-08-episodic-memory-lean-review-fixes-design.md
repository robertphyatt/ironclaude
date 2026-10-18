# Episodic Memory Lean — Final-Review Fixes Design

> **Created:** 2026-10-08
> **Status:** Design Complete
> **Requirements:** `docs/plans/2026-10-08-episodic-memory-lean-review-fixes-requirements.md`

Loop: full — touches index DB writes (slim, backfill) and the archive copy path where data can be lost.

## Summary

The Fable end-of-work review of the episodic-memory lean loop failed on one narrow finding (M1) and raised observations. The operator chose to fix M1 plus items 2, 5 and a guard for item 3 before committing. All four fixes are fail-safe and lossless. No new phases, settings or tools.

## Architecture

Changes stay inside the existing modules of `worker/mcp-servers/episodic-memory/src/`:

- `maintain.ts` — slim guard ordering (M1), compaction guard (item 3b), superseded files in `listCompactable` (R7), backfill unreadable handling (item 2) and legacy-index skip (item 5).
- `parser.ts`, `codex-parser.ts` — reject on stream error (item 2).
- `archive-io.ts` — `copyIntoArchive` keeps a larger raw copy (item 3a).
- `README.md` — superseded copies (R8).

## Components

### M1 — stale partial slim build (R1)

In `slimIndex`, after the "already slim" branch and the "no `tool_calls`" branch, before the disk guard (`maintain.ts:253-258`):

- If `db.sqlite.new`, `-wal` or `-shm` exist:
  - real run: remove them (force), then measure free disk;
  - dry run: change nothing; add their sizes to the free figure used by the guard and append `stale partial build <bytes> bytes would be removed` to the report.
- The resume branch (`:211-222`) rolling back with `verified=false` falls through to this same cleanup.

### Item 2 — damaged `.zst` fails fast (R2, R3)

- Both parsers attach `stream.once('error', e => { failure = e; rl.close(); })` before iterating, and throw `failure` after the loop. A partial parse is never returned.
- `backfillVoice` wraps the per-session parse in try/catch. On error: `counts.unreadable++`, advance the cursor, `continue` — no stamp, no insert, no delete for that session. The report adds `unreadable archive files N`.
- Rationale: a silent partial parse would make `stale = have − fresh` delete rows whose transcript simply failed to decode.

### Item 5 — never-slimmed index (R4)

`backfillVoice` first opens the DB read-only (dry and real runs alike). If `tool_calls` exists it returns `skipped: the index has not been slimmed yet; run maintain (slim) first` before `initDatabase` runs — so `maintain --only=backfill` refuses cleanly in both modes. One exception, approved by the operator (2026-10-09): a full `maintain --dry-run` reports backfill as `would-run … runs after the slim rebuilds the index`, because the full run slims first. `runMaintain` signals this with an internal option `afterSlim: !opts.only`; nothing else sets it. A real full run never reaches the guard with `tool_calls` present (the slim removed it, or `slimBlocked` skipped backfill). `maintain-run.test.ts:124-135` keeps passing.

### Item 3 — never lose a larger archive copy (R5, R6, R7)

- **(a) raw over raw.** `copyIntoArchive(src, dest)`: when `dest` exists as a raw file and `dest.size > src.size`: if `<dest>.superseded-<Math.trunc(dest.mtimeMs)>` (raw or `.zst`) exists, throw (sync records the error; nothing is overwritten). Otherwise copy `src` to the temp file first, then rename `dest` to its superseded name, then rename the temp file to `dest` — the logical path is never left empty (operator-approved 2026-10-09).
- **(b) recompression over a compacted copy.** In `compactOne`, after the crash-resume block and before writing the new `.zst`: if `<file>.zst` exists, stream-decode it once, computing its decoded byte count and sha256 (a decode error counts as "keep it"). If the decoded size is larger than the raw (or it failed to decode), rename the old `.zst` to `<file>.superseded-<Math.trunc(oldZstMtimeMs)>.zst` (throw if taken) and append a manifest line for the logical path `<rel>.superseded-<ts>` (its sha256 and rawBytes when decoded; this keeps it in the backup mirror, which walks the manifest). Otherwise replace as today.
- **Superseded naming** (`<session>.jsonl.superseded-<digits>[.zst]`) ends in neither `.jsonl` nor `.jsonl.zst`, so the indexer, verify, repair and summary paths never list it. `listCompactable` additionally accepts names matching `/\.jsonl\.superseded-\d+$/` so they are compacted with a manifest line.
- **Known limitation:** index rows built from the old content keep the session's logical path, so `read` shows the current content; the old bytes are in the superseded file.

Rejected: skipping the copy of a smaller source (loses the new content); byte-prefix comparison on every copy (reads multi-GB live files per sync; the operator chose the size rule).

## Data Flow

Unchanged except: sync may create one superseded file beside a session when its source shrinks; compaction may rename an old `.zst` to a superseded name before writing the new one; backfill may skip sessions as unreadable or skip the whole phase on a legacy index.

## Error Handling

- Every new path fails toward keeping data: name collisions throw instead of overwriting; a decode error on the old `.zst` keeps it; an unparseable session keeps its rows.
- Errors surface in the existing places: sync `result.errors`, compaction `manifest-errors.jsonl` (via the existing error path), and the phase report.

## Testing Strategy

TDD, each test RED first, all in temp dirs:

- M1: seed a partial `.new`; free disk set so the guard refuses only when the partial counts; real run proceeds and the partial is gone; dry run leaves it and reports it.
- Item 2: each parser rejects on a damaged `.zst` (vitest timeout catches a hang); backfill with one damaged and one good session processes the good one, keeps the damaged one's rows, reports `unreadable archive files 1`.
- Item 5: legacy fixture, `runMaintain({ only: 'backfill' })` and `runMaintain({ only: 'backfill', dryRun: true })` both report skipped and leave the legacy index unmigrated (no `meta` table, no `session_class` column).
- Item 3a: larger archive + smaller newer source keeps both byte-exact; smaller archive + larger source replaces with no superseded file; collision throws and leaves the archive unchanged.
- Item 3b: larger old `.zst` + smaller newer raw → both decode byte-exact after compaction; normal growth replaces the `.zst`.
- R7: a superseded file is compacted with a manifest line; verify's listing ignores it.

## Implementation Notes

- Bash cwd discipline, `git -C`, `git add -f` for docs, absolute paths: same execution invariants as the parent plan.
- The operator's opt-out file is not touched; nothing runs against `~/.config/superpowers`.
- After execution: full suite, then a final Fable review before the operator commits.
